import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Document, Page } from "react-pdf";
import { api } from "../api/client";
import type { ManagedAttachmentInfo } from "../types";
import { MAX_PREVIEW_BYTES, PREVIEW_TIMEOUT_MS } from "../lib/attachment-formats";
import { AttachmentImage } from "./AttachmentImage";
import { validateImageDimensions } from "../lib/image-dimensions";
import { ResourcePreviewShell } from "./ResourcePreviewShell";
import { Button } from "./ui/button";
import { useAuth } from "../context/AuthContext";

const AttachmentModel = lazy(() => import("./AttachmentModel"));
const modelsEnabled = import.meta.env.VITE_ATTACHMENT_MODEL_PREVIEW !== "false";

export default function AttachmentPreview({ versionId, title, onClose, backLabel, returnFocusTarget }: { versionId: string; title: string; onClose: () => void; backLabel?: string | null; returnFocusTarget?: HTMLElement | null }) {
  const { state: auth } = useAuth();
  const permissions = auth.status === "authed" ? auth.user.content_permissions || [] : [];
  const admin = auth.status === "authed" && auth.user.role === "admin";
  const canDownload = admin || permissions.includes("item.download");
  const canAssociate = admin || permissions.includes("item.publish");
  const [info, setInfo] = useState<ManagedAttachmentInfo | null>(null);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [success, setSuccess] = useState("");
  const upload = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    let active = true; let objectUrl = "";
    const timer = window.setTimeout(() => controller.abort(), PREVIEW_TIMEOUT_MS);
    setLoading(true); setError(""); setInfo(null); setBlob(null); setUrl(""); setPage(1);
    void (async () => {
      const metadata = await api.managedAttachmentInfo(versionId, controller.signal);
      if (!active) return;
      setInfo(metadata);
      if (metadata.preview_kind === "none") return;
      if (["dxf", "ifc"].includes(metadata.preview_kind) && !modelsEnabled) throw new Error("轻量模型预览已停用，请下载原文件或关联同版本预览");
      const content = await api.managedAttachmentBytes(versionId, true, controller.signal);
      if (metadata.preview_kind === "image") await validateImageDimensions(content);
      if (!active) return;
      controller.signal.throwIfAborted();
      setBlob(content); objectUrl = URL.createObjectURL(content); setUrl(objectUrl);
    })().catch((reason) => { if (active) setError(controller.signal.aborted ? "预览请求超时，可重试或下载原文件" : reason instanceof Error ? reason.message : "预览加载失败"); })
      .finally(() => { window.clearTimeout(timer); if (active) setLoading(false); });
    return () => { active = false; controller.abort(); window.clearTimeout(timer); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [versionId, revision]);
  const download = async () => {
    setBusy(true); setError("");
    try {
      const result = await api.downloadManagedContentFile(versionId, info?.original_filename || title);
      const link = document.createElement("a"); const href = URL.createObjectURL(result.blob);
      link.href = href; link.download = result.filename; link.click(); window.setTimeout(() => URL.revokeObjectURL(href), 1000);
      setSuccess("原文件下载已开始");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "下载失败"); } finally { setBusy(false); }
  };
  return <ResourcePreviewShell open returnFocusTarget={returnFocusTarget} title={title} subtitle="版本资料预览 · 不参与知识问答" onClose={onClose} backAction={backLabel ? <Button size="sm" variant="ghost" onClick={onClose}>{backLabel}</Button> : undefined}>
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border p-3">
        <Button size="sm" variant="outline" disabled={!canDownload || busy} title={!canDownload ? "无下载权限" : "下载当前版本原文件"} onClick={() => void download()}>{busy ? "处理中…" : "下载原文件"}</Button>
        <Button size="sm" variant="outline" disabled={loading || busy} onClick={() => setRevision((value) => value + 1)}>重试预览</Button>
        {canAssociate && <><Button size="sm" variant="outline" disabled={busy || loading} onClick={() => upload.current?.click()}>关联预览</Button><input ref={upload} type="file" accept=".jpg,.jpeg,.png,.pdf" aria-label="选择同版本 JPG、PNG 或 PDF 预览" className="sr-only" onChange={(event) => {
          const file = event.target.files?.[0]; event.target.value = "";
          if (!file) return;
          if (!/\.(jpe?g|png|pdf)$/i.test(file.name) || file.size > MAX_PREVIEW_BYTES) { setError("仅支持不超过 32 MB 的 JPG、PNG 或 PDF"); return; }
          setBusy(true); setError("");
          void api.uploadManagedAttachmentPreview(versionId, file).then(() => { setSuccess("已关联当前版本预览，不影响其他版本"); setRevision((value) => value + 1); }).catch((reason) => setError(reason instanceof Error ? reason.message : "关联预览失败")).finally(() => setBusy(false));
        }} /></>}
        {info && <p className="min-w-0 break-words text-xs text-muted-foreground">{info.doc_type.toUpperCase()} · {(info.file_size / 1024 / 1024).toFixed(2)} MB {info.version_number ? `· v${info.version_number}` : ""} · 仅作资料存档</p>}
      </div>
      {info && <details className="shrink-0 border-b border-border px-3 py-2 text-xs"><summary className="cursor-pointer rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring">文件信息 · {info.is_associated ? "当前版本关联预览" : info.preview_kind === "none" ? "暂不支持原生预览" : "原文件预览"}</summary><dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 break-words"><dt>文件名</dt><dd>{info.original_filename}</dd><dt>文件大小</dt><dd>{info.file_size.toLocaleString()} 字节</dd><dt>版本</dt><dd>{info.version_number ? `v${info.version_number}` : "所选版本"}{info.is_current ? "（当前已发布版本）" : ["pending_publication", "publication_failed", "publishing"].includes(info.lifecycle_status || "") ? "（待发布版本）" : info.lifecycle_status === "superseded" ? "（历史版本）" : ""}</dd><dt>目录</dt><dd>{info.category_path || info.category_name || "—"}</dd><dt>来源</dt><dd>{({ web: "网页上传", server: "服务器导入", upload: "资料上传", import: "批量导入" } as Record<string, string>)[info.source_origin || ""] || "—"}</dd><dt>资料状态</dt><dd>{({ published: "已发布", pending_publication: "待发布", publishing: "发布中", archived: "回收站", publication_failed: "发布失败" } as Record<string, string>)[info.lifecycle_status || ""] || "资料存档"}</dd><dt>创建时间</dt><dd>{info.created_at ? new Date(info.created_at * 1000).toLocaleString("zh-CN") : "—"}</dd><dt>知识问答</dt><dd>不参与 · 不创建检索索引</dd></dl></details>}
      {success && <p role="status" className="px-3 py-1 text-xs">{success}</p>}
      {error && <p role="alert" className="break-words p-3 text-sm text-destructive">{error}</p>}
      {loading ? <p role="status" className="p-6 text-sm">正在读取当前版本资料…</p> : info?.preview_kind === "none" ? <div className="p-6 text-sm"><p>此格式暂不支持浏览器原生预览。</p><p className="mt-2 text-muted-foreground">请下载后使用原生软件查看，或由发布负责人关联当前版本的 JPG、PNG、PDF 预览。更换版本不会沿用旧预览。</p></div> : url && blob ? <div className="min-h-0 flex-1">
        {info?.preview_kind === "image" ? <AttachmentImage key={url} url={url} title={title} /> : info?.preview_kind === "pdf" ? <div className="h-full overflow-auto p-2"><div className="mb-2 flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</Button><span className="text-sm">{page} / {pages}</span><Button size="sm" variant="outline" disabled={page >= pages} onClick={() => setPage(page + 1)}>下一页</Button></div><Document file={url} onLoadSuccess={({ numPages }) => setPages(numPages)} onLoadError={() => setError("PDF 预览失败，请下载原文件")}><Page pageNumber={page} width={Math.min(window.innerWidth - 48, 800)} /></Document></div> : info && ["dxf", "ifc"].includes(info.preview_kind) ? <Suspense fallback={<p role="status" className="p-4">正在加载本地查看器…</p>}><AttachmentModel key={url} blob={blob} kind={info.preview_kind as "dxf" | "ifc"} /></Suspense> : null}
      </div> : null}
    </div>
  </ResourcePreviewShell>;
}
