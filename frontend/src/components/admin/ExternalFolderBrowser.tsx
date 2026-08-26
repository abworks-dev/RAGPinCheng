import { useEffect, useState } from "react";
import { ChevronLeft, Folder, Play } from "lucide-react";
import { api } from "../../api/client";
import type { ExternalMediaEntry } from "../../types";
import { Button } from "../ui/button";
import { EmptyState } from "../ui/empty-state";
import { LoadingState } from "../ui/loading-state";

type Props = { sourceId: string; title?: string; readOnly?: boolean };

export function ExternalFolderBrowser({ sourceId, title = "远程目录", readOnly = true }: Props) {
  const [parent, setParent] = useState("");
  const [entries, setEntries] = useState<ExternalMediaEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const parentUp = parent.includes("/") ? parent.slice(0, parent.lastIndexOf("/")) : "";

  useEffect(() => {
    setParent("");
  }, [sourceId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void api.listExternalMediaEntries(sourceId, parent).then((result) => {
      if (!cancelled) { setEntries(result.entries); setError(null); }
    }).catch((cause) => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : "远程目录加载失败");
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [parent, sourceId]);

  return <section aria-label={title}>
    <div className="flex items-center gap-2 border-b border-border bg-surface-muted/40 px-4 py-2 text-ui-xs sm:px-5">
      <Button size="sm" variant="ghost" disabled={!parent} onClick={() => setParent(parentUp)}><ChevronLeft className="size-4" />上一级</Button>
      <span className="min-w-0 flex-1 truncate text-muted-foreground" title={parent || "根目录"}>{parent || "根目录"}</span>
      <span className="shrink-0 text-muted-foreground">远程只读</span>
    </div>
    {loading ? <LoadingState className="min-h-48" label="正在读取远程目录…" /> : error ? <p className="p-4 text-ui-sm text-destructive" role="alert">{error}</p> : entries.length === 0 ? <EmptyState title="当前目录没有视频" description="远程目录扫描后才会显示文件和子文件夹。" /> : <>
      <div className="hidden overflow-x-auto lg:block"><table className="w-full min-w-[56rem] text-ui-sm"><thead className="border-b border-border bg-surface-muted text-left text-muted-foreground"><tr><th className="w-20 px-3 py-3">类型</th><th className="px-3 py-3">资料</th><th className="w-36 px-3 py-3">状态</th><th className="w-32 px-3 py-3">来源</th><th className="w-24 px-3 py-3 text-right">操作</th></tr></thead><tbody className="divide-y divide-border">{entries.map((entry) => <tr key={entry.id} className="transition-colors hover:bg-surface-muted/60"><td className="px-3 py-3">{entry.kind === "folder" ? <Folder className="size-5 text-primary" aria-hidden="true" /> : <Play className="size-5 text-primary" aria-hidden="true" />}</td><td className="px-3 py-3"><button type="button" className="block max-w-full text-left" disabled={entry.kind !== "folder" || !readOnly} onClick={() => entry.kind === "folder" && setParent(entry.relative_path)}><span className="block break-words font-medium">{entry.name}</span><span className="text-ui-xs text-muted-foreground">{entry.kind === "folder" ? "远程子文件夹" : entry.relative_path}</span></button></td><td className="px-3 py-3 text-muted-foreground">{entry.kind === "folder" ? "—" : entry.availability === "missing" ? "远程文件缺失" : "可用"}</td><td className="px-3 py-3">共享目录</td><td className="px-3 py-3 text-right">{entry.kind === "folder" ? <Button size="sm" variant="outline" onClick={() => setParent(entry.relative_path)}>打开</Button> : <span className="text-ui-xs text-muted-foreground">只读</span>}</td></tr>)}</tbody></table></div>
      <ul className="divide-y divide-border lg:hidden" aria-label="远程目录内容">{entries.map((entry) => <li key={entry.id} className="flex items-start gap-3 px-4 py-4 sm:px-5">{entry.kind === "folder" ? <Folder className="mt-0.5 size-5 text-primary" aria-hidden="true" /> : <Play className="mt-0.5 size-5 text-primary" aria-hidden="true" />}<button type="button" className="min-w-0 flex-1 text-left" disabled={entry.kind !== "folder" || !readOnly} onClick={() => entry.kind === "folder" && setParent(entry.relative_path)}><span className="block break-words font-medium">{entry.name}</span><span className="text-ui-xs text-muted-foreground">{entry.kind === "folder" ? "远程子文件夹" : entry.availability === "missing" ? "远程文件缺失" : "共享目录 · 只读"}</span></button></li>)}</ul>
    </>}
  </section>;
}
