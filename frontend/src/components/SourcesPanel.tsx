import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { CITATION_EVENT, CITATION_HOVER_EVENT, type CitationDetail, type CitationHoverDetail } from "./citations";
import type { Source } from "../types";
import { stripMarkdown } from "../utils/markdown";
import { timestampToSeconds, useVideoPlayer } from "../hooks/useVideoPlayer";
import { usePdfPreview } from "../hooks/usePdfPreview";

function stripHtml(text: string): string {
  return text.replace(/<[^>]*>/g, "");
}

function locator(s: Source): string {
  if (s.doc_type === "transcript" && s.start_time) return `🎬 @${s.start_time}`;
  const leaf = stripHtml((s.section_path || "").split(" > ").pop() || "");
  return `§${leaf || "(无)"}`;
}

function breadcrumbParts(section_path: string): string[] {
  return section_path.split(" > ").filter(Boolean).map(stripHtml);
}

function SourceCard({
  s,
  i,
  id,
  highlight,
  cardRef,
  conversationId,
  messageId,
  searchQuery,
}: {
  s: Source;
  i: number;
  id: string;
  highlight: boolean;
  cardRef: (el: HTMLLIElement | null) => void;
  conversationId: string | null;
  messageId: string;
  searchQuery?: string;
}) {
  const [reportOpen, setReportOpen] = useState(false);
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [copyErr, setCopyErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const PREVIEW_CHARS = 400;
  const cleanText = stripMarkdown(s.text);
  const truncated = cleanText.length > PREVIEW_CHARS;
  const crumbs = s.doc_type === "transcript" ? [] : breadcrumbParts(s.section_path || "");
  const hasBreadcrumb = crumbs.length > 1;
  const canExpand = truncated;

  // Highlight keywords from the search query in the source text.
  function highlightText(text: string): string {
    if (!searchQuery) return text;
    // Split query into individual meaningful keywords (Chinese chars + English words)
    const keywords = searchQuery
      .replace(/[^\w一-鿿]/g, " ")
      .split(/\s+/)
      .filter((k) => k.length >= 2)
      .sort((a, b) => b.length - a.length); // longer matches first to avoid partial overlap
    if (keywords.length === 0) return text;
    const escaped = keywords.map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    const pattern = new RegExp(`(${escaped.join("|")})`, "gi");
    return text.replace(pattern, "<mark class='bg-yellow-200 dark:bg-yellow-700/60 rounded px-0.5'>$1</mark>");
  }

  const highlightedText = highlightText(cleanText);

  // Hover → highlight citations in the message body (bidirectional sync).
  function handleMouseEnter() {
    window.dispatchEvent(
      new CustomEvent<CitationHoverDetail>(CITATION_HOVER_EVENT, {
        detail: { messageId, sourceIndex: i },
      }),
    );
  }
  function handleMouseLeave() {
    window.dispatchEvent(
      new CustomEvent<CitationHoverDetail>(CITATION_HOVER_EVENT, {
        detail: { messageId, sourceIndex: null },
      }),
    );
  }

  async function submit() {
    setSubmitting(true);
    setErr(null);
    try {
      await api.sendFeedback({
        kind: "citation",
        note: note.trim() || undefined,
        conversation_id: conversationId,
        message_id: messageId,
        parent_id: s.parent_id,
        doc_title: s.doc_title,
        section_path: s.section_path,
        start_time: s.start_time,
        category: s.category,
      });
      setSent(true);
      setReportOpen(false);
      setNote("");
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <li
      id={id}
      ref={cardRef}
      className={
        "border-l-2 pl-3 transition-all duration-300 cursor-pointer " +
        (highlight
          ? "border-accent bg-blue-50 dark:bg-blue-900/20"
          : "border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800/50")
      }
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="font-medium text-ink">
          {i + 1}. [{s.doc_title}] <span className="text-muted">{locator(s)}</span>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            type="button"
            title="复制来源"
            onClick={async () => {
              try {
                const text = `[${s.doc_title}] ${locator(s)}\n${s.section_path || ""}\n\n${stripMarkdown(s.text)}`;
                if (navigator.clipboard?.writeText) {
                  await navigator.clipboard.writeText(text);
                } else {
                  // Fallback for HTTP (non-secure context)
                  const ta = document.createElement("textarea");
                  ta.value = text;
                  ta.style.position = "fixed";
                  ta.style.opacity = "0";
                  document.body.appendChild(ta);
                  ta.select();
                  document.execCommand("copy");
                  document.body.removeChild(ta);
                }
                setCopied(true);
                setCopyErr(null);
                window.setTimeout(() => setCopied(false), 1500);
              } catch (e: any) {
                setCopyErr(e?.message || "复制失败，请手动复制");
              }
            }}
            className="text-xs text-muted hover:text-accent transition-colors"
          >
            {copied ? "✓ 已复制" : "📋 复制"}
          </button>
          {(s.doc_type === "pdf" || s.doc_type === "docx" || s.doc_type === "xlsx" || s.doc_type === "pptx") && (
            <PdfPreviewButton
            parentId={s.parent_id} title={s.doc_title} docType={s.doc_type}
            sheetName={s.sheet_name} cellRange={s.cell_range}
            slideNumber={s.slide_number} paragraphAnchor={s.paragraph_anchor} />
          )}
          <button
            type="button"
            title="报告引用有误"
            onClick={() => setReportOpen((v) => !v)}
            className="text-xs text-muted hover:text-red-600"
          >
            {sent ? "已报告" : "⚠ 报错"}
          </button>
        </div>
      </div>
      {copyErr && <div className="text-xs text-red-600 mt-1">{copyErr}</div>}
      {hasBreadcrumb && (
        <div className="text-xs text-muted mt-1 leading-relaxed break-words">
          § {crumbs.join(" › ")}
        </div>
      )}
      <div
        className={
          "text-xs text-gray-600 mt-1 whitespace-pre-wrap " +
          (expanded ? "max-h-96 overflow-y-auto pr-1" : "line-clamp-6")
        }
        dangerouslySetInnerHTML={{
          __html: expanded || !truncated
            ? highlightedText
            : highlightText(cleanText.slice(0, PREVIEW_CHARS)) + "…",
        }}
      />
      {canExpand && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 text-xs text-accent hover:underline"
        >
          {expanded ? "收起" : "展开"}
        </button>
      )}
      {/* Video play button — only for transcripts with media */}
      {s.doc_type === "transcript" && s.media_id && (
        <SourcePlayButton
          mediaId={s.media_id}
          title={s.doc_title}
          startTime={s.start_time}
        />
      )}
      {reportOpen && (
        <div className="mt-2 flex flex-col gap-1.5">
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="可选：为什么这条引用有误？"
            rows={2}
            className="border border-gray-300 rounded p-2 text-xs bg-white dark:bg-gray-800"
          />
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={submit}
              disabled={submitting}
              className="px-2 py-1 rounded bg-red-600 text-white text-xs disabled:opacity-50"
            >
              {submitting ? "提交中…" : "提交报告"}
            </button>
            {err && <span className="text-xs text-red-600">{err}</span>}
          </div>
        </div>
      )}
    </li>
  );
}

/**
 * PDF preview button. Opens the PDF preview panel for this source.
 */
function PdfPreviewButton({ parentId, title, docType, sheetName, cellRange, slideNumber, paragraphAnchor }: {
  parentId: string;
  title: string;
  docType?: string;
  sheetName?: string | null;
  cellRange?: string | null;
  slideNumber?: number | null;
  paragraphAnchor?: string | null;
}) {
  const { open } = usePdfPreview();
  const isDocx = docType === "docx";
  const isXlsx = docType === "xlsx";
  const isPptx = docType === "pptx";

  function handleClick(e: React.MouseEvent) {
    e.stopPropagation();
    const initialPage = (docType === "pptx" open(parentId, title, docType || "pdf", 1, { sheetName, cellRange, slideNumber, paragraphAnchor });open(parentId, title, docType || "pdf", 1, { sheetName, cellRange, slideNumber, paragraphAnchor }); slideNumber) ? slideNumber : 1;
    open(parentId, title, docType || "pdf", initialPage, { sheetName, cellRange, slideNumber, paragraphAnchor });
  }

  const label = isDocx ? "DOCX 预览" : isXlsx ? "XLSX 预览" : isPptx ? "PPTX 预览" : "PDF 预览";
  const icon = isDocx ? "📄" : isXlsx ? "📊" : isPptx ? "📽️" : "📄";

  return (
    <button
      type="button"
      onClick={handleClick}
      className="text-xs text-accent hover:underline"
      title={label}
    >
      {icon} {label}
    </button>
  );
}

/**
 * Play button for transcript sources. Opens the video player drawer
 * at the timestamp corresponding to this source chunk.
 */
function SourcePlayButton({
  mediaId,
  title,
  startTime,
}: {
  mediaId: string;
  title: string;
  startTime: string | null;
}) {
  const { open } = useVideoPlayer();

  function handlePlay(e: React.MouseEvent) {
    e.stopPropagation();
    open({
      mediaId,
      title,
      startSeconds: timestampToSeconds(startTime),
      fromSource: true,
    });
  }

  return (
    <button
      type="button"
      onClick={handlePlay}
      className="mt-2 flex items-center gap-1.5 text-xs text-accent hover:underline"
    >
      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
        <circle cx="12" cy="12" r="10" fillOpacity={0.15} />
        <path d="M9.5 7.5l7 4.5-7 4.5v-9z" />
      </svg>
      从 {startTime || "00:00:00"} 播放
    </button>
  );
}

export function SourcesPanel({
  sources,
  messageId,
  conversationId,
  searchQuery,
}: {
  sources: Source[];
  messageId: string;
  conversationId: string | null;
  searchQuery?: string;
}) {
  const [open, setOpen] = useState(false);
  const [highlightIdx, setHighlightIdx] = useState<number | null>(null);
  const refs = useRef<Record<number, HTMLLIElement | null>>({});

  useEffect(() => {
    function onCitation(e: Event) {
      const detail = (e as CustomEvent<CitationDetail>).detail;
      if (!detail || detail.messageId !== messageId) return;
      setOpen(true);
      setHighlightIdx(detail.sourceIndex);
      // Wait for the panel to expand, then scroll the card into view.
      requestAnimationFrame(() => {
        const el = refs.current[detail.sourceIndex];
        if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
      });
      window.setTimeout(() => setHighlightIdx(null), 1800);
    }
    window.addEventListener(CITATION_EVENT, onCitation);
    return () => window.removeEventListener(CITATION_EVENT, onCitation);
  }, [messageId]);

  if (!sources?.length) return null;

  // Group sources by category, preserving original order within each group.
  const CATEGORY_LABELS: Record<string, string> = {
    "教学视频": "🎬 教学视频",
    "培训视频": "🎬 培训视频",
    "公司标准": "📋 公司标准",
    "客户标准": "📋 客户标准",
    "行业规范": "📋 行业规范",
    "设计规范": "📋 设计规范",
    "uncategorized": "📄 其他",
  };
  const groups: { category: string; sources: Source[]; indices: number[] }[] = [];
  const groupMap = new Map<string, { category: string; sources: Source[]; indices: number[] }>();
  sources.forEach((s, i) => {
    const cat = s.category || "uncategorized";
    let g = groupMap.get(cat);
    if (!g) {
      g = { category: cat, sources: [], indices: [] };
      groupMap.set(cat, g);
      groups.push(g);
    }
    g.sources.push(s);
    g.indices.push(i);
  });

  return (
    <div className="mt-3 border border-gray-200 rounded-lg bg-white/60">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full text-left px-3 py-2 text-sm text-muted hover:bg-gray-50 rounded-lg flex items-center justify-between"
      >
        <span>📎 参考来源 ({sources.length})</span>
        <span className="text-xs">{open ? "收起" : "展开"}</span>
      </button>
      {open && (
        <div className="px-4 py-2 space-y-4 text-sm">
          {groups.map((g) => (
            <GroupedSources
              key={g.category}
              category={g.category}
              label={CATEGORY_LABELS[g.category] || `📄 ${g.category}`}
              sources={g.sources}
              indices={g.indices}
              messageId={messageId}
              conversationId={conversationId}
              searchQuery={searchQuery}
              highlightIdx={highlightIdx}
              cardRefs={refs}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function GroupedSources({
  category,
  label,
  sources,
  indices,
  messageId,
  conversationId,
  searchQuery,
  highlightIdx,
  cardRefs,
}: {
  category: string;
  label: string;
  sources: Source[];
  indices: number[];
  messageId: string;
  conversationId: string | null;
  searchQuery?: string;
  highlightIdx: number | null;
  cardRefs: React.MutableRefObject<Record<number, HTMLLIElement | null>>;
}) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div>
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        className="w-full text-left flex items-center gap-1.5 text-xs font-medium text-muted hover:text-ink transition-colors mb-1.5"
      >
        <span className="text-[10px]">{collapsed ? "▶" : "▼"}</span>
        <span>{label}</span>
        <span className="text-[10px] opacity-60">({sources.length})</span>
      </button>
      {!collapsed && (
        <ol className="space-y-3">
          {sources.map((s, j) => {
            const globalIdx = indices[j];
            return (
              <SourceCard
                key={s.parent_id + j}
                s={s}
                i={globalIdx}
                id={`src-${messageId}-${globalIdx}`}
                highlight={highlightIdx === globalIdx}
                cardRef={(el) => { cardRefs.current[globalIdx] = el; }}
                conversationId={conversationId}
                messageId={messageId}
                searchQuery={searchQuery}
              />
            );
          })}
        </ol>
      )}
    </div>
  );
}
