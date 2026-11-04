import { useEffect, useRef, useState } from "react";
import { FileText } from "lucide-react";
import { api } from "../api/client";
import type { FileResult } from "../types";
import { MAX_PREVIEW_BYTES, PREVIEW_TIMEOUT_MS } from "../lib/attachment-formats";
import { validateImageDimensions } from "../lib/image-dimensions";

/** Images only: never instantiate PDF/model viewers or follow metadata URLs. */
export function AttachmentThumbnail({ file }: { file: FileResult }) {
  const target = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [url, setUrl] = useState("");
  const [state, setState] = useState("暂无图片封面");
  const allowed = file.can_preview && file.preview_kind === "image";
  useEffect(() => {
    if (!allowed || !target.current || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect(); }
    }, { rootMargin: "0px", threshold: 0.01 });
    observer.observe(target.current);
    return () => observer.disconnect();
  }, [allowed, file.version_id]);
  useEffect(() => {
    if (!allowed || !visible) return;
    const controller = new AbortController();
    let active = true; let objectUrl = "";
    const timer = window.setTimeout(() => controller.abort(), PREVIEW_TIMEOUT_MS);
    setUrl(""); setState("正在加载封面…");
    void (async () => {
      const info = await api.managedAttachmentInfo(file.version_id, controller.signal, "chat");
      if (!active) return;
      if (info.can_preview === false || info.preview_kind !== "image") { setState("暂无图片封面"); return; }
      // Associated previews may be smaller than the original model; guard actual bytes below.
      if (!info.is_associated && info.file_size > MAX_PREVIEW_BYTES) throw new Error("封面超过大小限制");
      const blob = await api.managedAttachmentBytes(file.version_id, true, controller.signal, "chat");
      if (blob.size > MAX_PREVIEW_BYTES) throw new Error("封面超过大小限制");
      await validateImageDimensions(blob);
      if (!active) return;
      controller.signal.throwIfAborted();
      objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); setState("");
    })().catch(() => { if (active) setState("封面加载失败，可打开预览重试"); })
      .finally(() => window.clearTimeout(timer));
    return () => { active = false; controller.abort(); window.clearTimeout(timer); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [allowed, visible, file.version_id]);
  return <div ref={target} className="flex h-full min-w-0 items-center justify-center bg-surface-muted p-2">
    {allowed && url ? <img src={url} alt={`${file.title || file.original_filename}的图片封面`} className="h-full w-full object-contain" onError={() => { setUrl(""); setState("封面加载失败，可打开预览重试"); }} /> : <div className="flex min-w-0 flex-col items-center gap-2 text-center text-muted-foreground"><FileText aria-hidden="true" className="h-8 w-8" /><span className="text-xs font-medium">{file.doc_type.toUpperCase()}</span><span className="text-xs" role={state.startsWith("正在") ? "status" : undefined}>{state}</span></div>}
  </div>;
}
