import { useEffect, useId, useState } from "react";
import { api } from "../api/client";
import type { AttachmentOcrState, AttachmentSearchMetadata } from "../types";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

const statusLabels: Record<string, string> = { pending: "等待识别", running: "正在识别", succeeded: "识别成功", no_text: "未识别到文字", failed: "识别失败", cancelled: "已取消", stale: "结果已失效" };
const errorLabels: Record<string, string> = { ocr_disabled: "OCR 服务未启用，请联系管理员", no_text: "未识别到文字", timeout: "识别超时，请重试", source_changed: "原文件已变化，请重新识别" };
export function AttachmentSearchPanel({ versionId, docType, canEdit }: { versionId: string; docType: string; canEdit: boolean }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [metadata, setMetadata] = useState<AttachmentSearchMetadata | null>(null);
  const [ocr, setOcr] = useState<AttachmentOcrState | null>(null);
  const [description, setDescription] = useState(""); const [project, setProject] = useState("");
  const [tags, setTags] = useState(""); const [aliases, setAliases] = useState("");
  const [loading, setLoading] = useState(false); const [busy, setBusy] = useState("");
  const [error, setError] = useState(""); const [success, setSuccess] = useState("");
  const [revision, setRevision] = useState(0);
  const image = ["jpg", "jpeg", "png"].includes(docType.toLowerCase());
  const latest = ocr?.jobs[0]; const processing = latest?.status === "pending" || latest?.status === "running";
  const ocrUnavailable = ocr?.enabled === false || ocr?.available === false;
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController(); let active = true;
    setLoading(true); setError("");
    void Promise.allSettled([api.attachmentSearchInfo(versionId, controller.signal), image ? api.attachmentOcr(versionId, controller.signal) : Promise.resolve(null)]).then(([metadataResult, ocrResult]) => {
      if (!active) return;
      const failures: string[] = [];
      if (metadataResult.status === "fulfilled") {
        const value = metadataResult.value;
        setMetadata(value); setDescription(value.description); setProject(value.project); setTags(value.tags.join("、")); setAliases(value.aliases.join("、"));
      } else failures.push(metadataResult.reason instanceof Error ? metadataResult.reason.message : "读取文件搜索信息失败");
      if (ocrResult.status === "fulfilled") setOcr(ocrResult.value);
      else failures.push("读取原图识别状态失败，请重新读取；文件搜索信息仍可维护。");
      setError(failures.join("；"));
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [open, versionId, revision, image]);
  useEffect(() => {
    if (!open || !processing) return;
    let active = true;
    const timer = window.setInterval(() => { void api.attachmentOcr(versionId).then(value => { if (active) setOcr(value); }).catch(() => { if (active) setError("识别状态更新失败，请刷新状态"); }); }, 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [open, processing, versionId]);
  const list = (value: string) => [...new Set(value.split(/[、,，\n]/).map(part => part.trim()).filter(Boolean))];
  const act = async (kind: "save" | "start" | "cancel") => {
    setBusy(kind); setError(""); setSuccess("");
    try {
      if (kind === "save") { const value = await api.updateAttachmentSearchMetadata(versionId, { description, project, tags: list(tags), aliases: list(aliases) }); setMetadata(value); setSuccess("当前版本文件搜索信息已保存"); }
      else { if (kind === "start") await api.startAttachmentOcr(versionId); else await api.cancelAttachmentOcr(versionId); setOcr(await api.attachmentOcr(versionId)); setSuccess(kind === "start" ? "原图文字识别已提交" : "已请求取消文字识别"); }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败，请重试"); }
    finally { setBusy(""); }
  };
  return <details open={open} onToggle={event => setOpen(event.currentTarget.open)} className="max-h-[45vh] shrink-0 overflow-auto border-b border-border px-3 py-2 text-xs">
    <summary className="cursor-pointer rounded font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring">文件搜索信息与原图文字识别</summary>
    <div className="mt-3 min-w-0 space-y-3">
      <p className="text-muted-foreground">仅修改当前版本。文件信息与已启用的 OCR 文字用于找附件，不作为正文问答依据。</p>
      {loading && <p role="status">正在读取文件搜索信息…</p>}
      {error && <div role="alert" className="break-words text-destructive">{error}<Button size="sm" variant="ghost" disabled={loading || !!busy} onClick={() => setRevision(value => value + 1)}>重新读取</Button></div>}
      {!loading && metadata && <form className="grid min-w-0 gap-3 sm:grid-cols-2" onSubmit={event => { event.preventDefault(); void act("save"); }}>
        <label className="min-w-0 space-y-1 sm:col-span-2" htmlFor={`${id}-description`}><span>描述</span><Input id={`${id}-description`} value={description} maxLength={4000} disabled={!canEdit || !!busy} onChange={event => setDescription(event.target.value)} /></label>
        <label className="min-w-0 space-y-1" htmlFor={`${id}-project`}><span>项目</span><Input id={`${id}-project`} value={project} maxLength={200} disabled={!canEdit || !!busy} onChange={event => setProject(event.target.value)} /></label>
        <label className="min-w-0 space-y-1" htmlFor={`${id}-tags`}><span>标签（顿号分隔）</span><Input id={`${id}-tags`} value={tags} disabled={!canEdit || !!busy} onChange={event => setTags(event.target.value)} /></label>
        <label className="min-w-0 space-y-1 sm:col-span-2" htmlFor={`${id}-aliases`}><span>别名（顿号分隔）</span><Input id={`${id}-aliases`} value={aliases} disabled={!canEdit || !!busy} onChange={event => setAliases(event.target.value)} /></label>
        <div className="flex flex-wrap items-center gap-2 sm:col-span-2"><Button type="submit" size="sm" disabled={!canEdit || !!busy}>{busy === "save" ? "保存中…" : "保存搜索信息"}</Button>{!canEdit && <span>无修改权限；上传者或发布负责人可维护。</span>}</div>
      </form>}
      <section aria-label="原图 OCR 状态" className="space-y-2 border-t border-border pt-3">
        <h3 className="font-medium">原图文字识别（OCR）</h3>
        {!image ? <p className="text-muted-foreground">仅支持原始 JPG、PNG。模型及其关联预览不自动识别；不解析 RVT、DWG、CHE 内容。</p> : <>
          {ocrUnavailable && <p className="text-muted-foreground">{ocr?.enabled === false ? "原图文字识别未启用，请联系管理员启用本地 OCR 服务。" : "本地 OCR 服务尚未完成配置，请联系管理员检查服务地址和凭据。"}已有有效文字结果不会因此自动删除。</p>}
          <p role="status">{latest ? statusLabels[latest.status] || "状态待刷新" : "尚未提交文字识别"}{ocr?.current_job_id ? " · 已有可搜索的有效文字结果" : " · 暂无有效文字结果"}</p>
          {latest?.status === "failed" && <p className="text-destructive">{errorLabels[latest.error_code || ""] || "识别未完成，请重试或联系管理员检查本地 OCR 服务。"}</p>}
          {latest?.status === "no_text" && <p>未找到可搜索文字，可检查原图清晰度后重试。此状态不表示已理解图片。</p>}
          <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={!canEdit || !!busy || loading || processing || ocrUnavailable} onClick={() => void act("start")}>{busy === "start" ? "提交中…" : latest ? "重新识别原图文字" : "识别原图文字"}</Button><Button size="sm" variant="outline" disabled={!canEdit || !!busy || !processing} onClick={() => void act("cancel")}>{busy === "cancel" ? "取消中…" : "取消识别"}</Button><Button size="sm" variant="ghost" disabled={!!busy || loading} onClick={() => setRevision(value => value + 1)}>刷新状态</Button></div>
        </>}
      </section>
      {success && <p role="status">{success}</p>}
    </div>
  </details>;
}
