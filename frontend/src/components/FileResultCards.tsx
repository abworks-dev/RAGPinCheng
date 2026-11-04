import { useState } from "react";
import type { FileResult } from "../types";
import { api } from "../api/client";
import { usePdfPreview } from "../hooks/usePdfPreview";
import { Button } from "./ui/button";

const fields: Record<string, string> = { title: "标题", original_filename: "文件名", description: "描述", tags: "标签", project: "项目", aliases: "别名", category_path: "分类", ocr_text: "OCR 文字" };
export function FileResultCards({ results }: { results: FileResult[] }) {
  return results.length ? <AttachmentCards results={results} /> : null;
}
function AttachmentCards({ results }: { results: FileResult[] }) {
  const { openAttachment } = usePdfPreview();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  if (!results.length) return null;
  const download = async (file: FileResult) => {
    setBusy(file.version_id); setError(""); setSuccess("");
    try {
      const result = await api.downloadChatAttachment(file.version_id, file.original_filename);
      const url = URL.createObjectURL(result.blob); const link = document.createElement("a");
      link.href = url; link.download = result.filename; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000); setSuccess("原文件下载已开始");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "下载失败，请重试"); }
    finally { setBusy(null); }
  };
  return <section aria-label="找到的附件" className="mt-4 min-w-0 space-y-2">
    <h3 className="text-sm font-medium">相关附件 · {results.length}</h3>
    <p className="text-xs text-muted-foreground">按文件信息或 OCR 文字匹配，不作为正文问答依据；未解析模型内容或理解图片。</p>
    <ul className="space-y-2">{results.map(file => <li key={file.version_id} className="min-w-0 rounded-ui-md border border-border bg-card p-3">
      <h4 className="break-words text-sm font-medium [overflow-wrap:anywhere]">{file.title || file.original_filename}</h4>
      <p className="mt-1 break-words text-xs text-muted-foreground [overflow-wrap:anywhere]">{file.original_filename} · {file.doc_type.toUpperCase()} · {(file.file_size / 1024 / 1024).toFixed(2)} MB · v{file.version_number}</p>
      {file.category_path && <p className="mt-1 break-words text-xs">{file.category_path}</p>}
      <p className="mt-2 break-words text-xs text-muted-foreground">匹配：{file.matched_fields.map(value => fields[value] || "文件信息").join("、") || "文件信息"}</p>
      {file.snippet && <p className="mt-1 whitespace-pre-wrap break-words text-sm [overflow-wrap:anywhere]">{file.snippet}</p>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" disabled={!file.can_preview} title={file.can_preview ? "预览所选版本" : "此附件暂无可用预览"} onClick={() => openAttachment(file.version_id, file.title || file.original_filename, file.doc_type)}>预览附件</Button>
        <Button size="sm" variant="outline" disabled={!file.can_download || busy !== null} title={!file.can_download ? "无原文件下载权限" : "下载所选版本"} onClick={() => void download(file)}>{busy === file.version_id ? "下载中…" : "下载原文件"}</Button>
        {!file.can_preview && <span className="text-xs text-muted-foreground">暂无可用预览</span>}
        {!file.can_download && <span className="text-xs text-muted-foreground">无下载权限</span>}
      </div>
    </li>)}</ul>
    {error && <p role="alert" className="break-words text-sm text-destructive">{error}</p>}
    {success && <p role="status" className="text-xs">{success}</p>}
  </section>;
}
