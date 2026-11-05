import { useId, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Download, Eye, MoreHorizontal } from "lucide-react";
import { IconButton } from "./ui/icon-button";
import type { FileResult } from "../types";
import { api } from "../api/client";
import { usePdfPreview } from "../hooks/usePdfPreview";
import { Button } from "./ui/button";
import { AttachmentThumbnail } from "./AttachmentThumbnail";

const fields: Record<string, string> = { title: "标题", original_filename: "文件名", description: "描述", tags: "标签", project: "项目", aliases: "别名", category_path: "分类", ocr_text: "OCR 文字" };
export function FileResultCards({ results }: { results: FileResult[] }) {
  return results.length ? <AttachmentCards results={results} /> : null;
}
function AttachmentCards({ results }: { results: FileResult[] }) {
  const { openAttachment } = usePdfPreview();
  const strip = useRef<HTMLUListElement>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const download = async (file: FileResult) => {
    if (!file.can_download || busy !== null) return;
    setBusy(file.version_id); setError(""); setSuccess("");
    try {
      const result = await api.downloadChatAttachment(file.version_id, file.original_filename);
      const url = URL.createObjectURL(result.blob); const link = document.createElement("a");
      link.href = url; link.download = result.filename; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000); setSuccess("原文件下载已开始");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "下载失败，请重试"); }
    finally { setBusy(null); }
  };
  const scroll = (direction: number) => strip.current?.scrollBy({ left: direction * 212, behavior: "smooth" });
  return <section aria-label="找到的附件" className="mt-4 w-full min-w-0 max-w-full space-y-2">
    <div className="flex min-w-0 items-center justify-between gap-2"><h3 className="text-sm font-medium">相关附件 · {results.length}</h3><div className="flex shrink-0 gap-1">
      <Button size="icon" variant="ghost" aria-label="向左滚动附件" title="向左滚动附件" onClick={() => scroll(-1)}><ChevronLeft aria-hidden="true" className="h-4 w-4" /></Button>
      <Button size="icon" variant="ghost" aria-label="向右滚动附件" title="向右滚动附件" onClick={() => scroll(1)}><ChevronRight aria-hidden="true" className="h-4 w-4" /></Button>
    </div></div>
    <p className="text-xs text-muted-foreground">按文件信息或 OCR 文字匹配，不作为正文问答依据；未解析模型内容或理解图片。</p>
    <ul ref={strip} aria-label="附件列表" className="flex w-full min-w-0 max-w-full snap-x gap-3 overflow-x-auto overscroll-x-contain py-1" onFocusCapture={event => { const card = (event.target as HTMLElement).closest("li"); card?.scrollIntoView?.({ block: "nearest", inline: "nearest" }); }}>
      {results.map(file => <AttachmentCard key={file.version_id} file={file} busy={busy} onDownload={() => void download(file)} onPreview={() => openAttachment(file.version_id, file.title || file.original_filename, file.doc_type)} />)}
    </ul>
    {error && <p role="alert" className="break-words text-sm text-destructive">{error}</p>}
    {success && <p role="status" className="text-xs">{success}</p>}
  </section>;
}
function AttachmentCard({ file, busy, onDownload, onPreview }: { file: FileResult; busy: string | null; onDownload: () => void; onPreview: () => void }) {
  const [expanded, setExpanded] = useState<boolean | null>(null);
  const id = useId();
  const title = file.title || file.original_filename;
  return <li className="group relative w-[12.5rem] min-w-0 max-w-full shrink-0 snap-start overflow-hidden rounded-ui-md border border-border bg-card" onFocusCapture={event => { if ((event.target as HTMLElement).matches(":focus-visible") && expanded !== false) setExpanded(true); }} onBlurCapture={event => { if (expanded !== true && !event.currentTarget.contains(event.relatedTarget as Node | null)) setExpanded(null); }} onKeyDown={event => { if (event.key === "Escape") { event.currentTarget.querySelector<HTMLButtonElement>("button")?.focus(); setExpanded(false); } }}>
    <div className="absolute bottom-0 right-0 z-10 bg-card/90 transition-opacity [@media(hover:hover)_and_(pointer:fine)]:opacity-0 [@media(hover:hover)_and_(pointer:fine)]:group-hover:opacity-100 [@media(hover:hover)_and_(pointer:fine)]:group-focus-within:opacity-100"><IconButton className="size-9" label={`更多信息：${title}`} tooltip="更多信息" aria-expanded={expanded === true} aria-controls={id} onClick={() => setExpanded(value => !value)}><MoreHorizontal aria-hidden="true" className="size-4" /></IconButton></div>
    <div className="relative h-[9.5rem] min-w-0">
      <AttachmentThumbnail file={file} />
      <div id={id} className={`absolute inset-0 flex min-w-0 flex-col bg-card/80 p-2 backdrop-blur-sm transition-opacity ${expanded === true ? "visible opacity-100" : expanded === false ? "invisible opacity-0" : "invisible opacity-0 group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"}`}>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto break-words text-xs [overflow-wrap:anywhere]" tabIndex={0} aria-label={`${title}的详细信息`}>
          <p className="font-medium">{file.original_filename}</p>
          <p>{file.category_path || "未分类"} · v{file.version_number} · {(file.file_size / 1024 / 1024).toFixed(2)} MB</p>
          <p className="text-muted-foreground">匹配：{file.matched_fields.map(value => fields[value] || "文件信息").join("、") || "文件信息"}</p>
          {file.snippet && <p className="whitespace-pre-wrap">{file.snippet}</p>}
          {!file.can_preview && <p>暂无可用预览</p>}{!file.can_download && <p>无下载权限</p>}
        </div>
        <div className="mt-2 flex shrink-0 flex-wrap gap-1">
          <IconButton label="预览附件" tooltip={file.can_preview ? "预览附件" : "此附件暂无可用预览"} disabled={!file.can_preview} className="border border-border bg-card/80 text-foreground" onClick={() => { setExpanded(true); onPreview(); }}><Eye aria-hidden="true" className="size-4" /></IconButton>
          <IconButton label="下载原文件" tooltip={!file.can_download ? "无原文件下载权限" : busy !== null ? "正在下载，请稍候" : "下载原文件"} disabled={!file.can_download || busy !== null} aria-busy={busy === file.version_id} className="border border-border bg-card/80 text-foreground" onClick={onDownload}><Download aria-hidden="true" className="size-4" /></IconButton>
        </div>
      </div>
    </div>
    <div className="flex h-9 min-w-0 items-center border-t border-border px-2 pr-10">
      <h4 title={`${title} · ${file.original_filename}`} className="truncate text-xs font-medium">{file.original_filename}</h4>
    </div>
  </li>;
}
