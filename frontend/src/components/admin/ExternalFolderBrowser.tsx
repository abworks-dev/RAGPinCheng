import { useEffect, useState } from "react";
import { ChevronLeft, Folder, Network, Play } from "lucide-react";
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

  return <section className="mt-4 overflow-hidden rounded-ui-md border border-border" aria-label={title}>
    <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
      <div className="flex min-w-0 items-center gap-2"><Network className="size-4 shrink-0 text-primary" aria-hidden="true" /><h3 className="truncate font-semibold">{title}</h3></div>
      <span className="text-ui-xs text-muted-foreground">远程只读</span>
    </div>
    <div className="flex items-center gap-2 border-b border-border px-4 py-2 text-ui-xs">
      <Button size="sm" variant="ghost" disabled={!parent} onClick={() => setParent(parentUp)}><ChevronLeft className="size-4" />上一级</Button>
      <span className="min-w-0 truncate text-muted-foreground" title={parent || "根目录"}>{parent || "根目录"}</span>
    </div>
    {loading ? <LoadingState className="min-h-28" label="正在读取远程目录…" /> : error ? <p className="p-4 text-ui-sm text-destructive" role="alert">{error}</p> : entries.length === 0 ? <EmptyState title="当前目录没有视频" description="远程目录扫描后才会显示文件和子文件夹。" /> : <ul className="divide-y divide-border" aria-label="远程目录内容">{entries.map((entry) => <li key={entry.id} className="flex items-center gap-3 px-4 py-3"><span className="shrink-0">{entry.kind === "folder" ? <Folder className="size-4 text-primary" aria-hidden="true" /> : <Play className="size-4 text-muted-foreground" aria-hidden="true" />}</span><button type="button" className="min-w-0 flex-1 text-left" disabled={entry.kind !== "folder" || !readOnly} onClick={() => entry.kind === "folder" && setParent(entry.relative_path)}><span className="block break-words text-ui-sm font-medium">{entry.name}</span><span className="block text-ui-xs text-muted-foreground">{entry.kind === "folder" ? "远程子文件夹" : entry.availability === "missing" ? "远程文件缺失" : "远程视频"}</span></button></li>)}</ul>}
  </section>;
}
