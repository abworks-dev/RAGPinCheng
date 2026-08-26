import { useEffect, useState } from "react";
import { ChevronDown, ChevronLeft, ChevronRight, Film, Folder, FolderOpen } from "lucide-react";
import { api } from "../../api/client";
import type { ExternalMediaEntry } from "../../types";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { EmptyState } from "../ui/empty-state";
import { LoadingState } from "../ui/loading-state";
import { ManagedItemType } from "./ManagedItemType";

type BrowserProps = { sourceId: string; title?: string };

function useExternalEntries(sourceId: string, parent: string) {
  const [entries, setEntries] = useState<ExternalMediaEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void api.listExternalMediaEntries(sourceId, parent).then((result) => {
      if (!cancelled) { setEntries(result.entries); setError(null); }
    }).catch((cause) => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : "共享目录加载失败");
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [parent, sourceId]);
  return { entries, loading, error };
}

function externalUpdatedAt(entry: ExternalMediaEntry) {
  if (!entry.modified_ns) return "—";
  const date = new Date(entry.modified_ns / 1_000_000);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function availabilityLabel(entry: ExternalMediaEntry) {
  if (entry.kind === "folder") return "共享只读";
  if (entry.availability === "missing") return "远程文件缺失";
  if (entry.availability === "superseded") return "已更新";
  return "可用 · 只读";
}

export function ExternalFolderBrowser({ sourceId, title = "共享目录" }: BrowserProps) {
  const [parent, setParent] = useState("");
  const { entries, loading, error } = useExternalEntries(sourceId, parent);
  const parentUp = parent.includes("/") ? parent.slice(0, parent.lastIndexOf("/")) : "";
  useEffect(() => { setParent(""); }, [sourceId]);
  return <section aria-label={title}>
    <div className="flex items-center gap-2 border-b border-border bg-surface-muted/40 px-4 py-2 text-ui-xs sm:px-5">
      <Button size="sm" variant="ghost" disabled={!parent} onClick={() => setParent(parentUp)}><ChevronLeft className="size-4" />上一级</Button>
      <span className="min-w-0 flex-1 truncate text-muted-foreground" title={parent || "根目录"}>{parent || "根目录"}</span>
      <Badge variant="secondary">共享</Badge>
    </div>
    {loading ? <LoadingState className="min-h-48" label="正在读取共享目录…" /> : error ? <p className="p-4 text-ui-sm text-destructive" role="alert">{error}</p> : entries.length === 0 ? <EmptyState title="当前目录暂无资料" description="共享目录扫描后会在这里显示直属文件夹和文件。" /> : <>
      <div className="hidden overflow-x-auto border-t border-border lg:block"><table className="w-full min-w-[72rem] text-ui-sm">
        <thead className="border-b border-border bg-surface-muted text-left text-muted-foreground"><tr>
          <th className="w-8 px-1.5 py-3"><Checkbox aria-label="共享资料不可批量选择" disabled /></th>
          <th className="w-16 px-1 py-3 text-center font-medium">类型</th><th className="px-1.5 py-3 font-medium">资料</th><th className="px-3 py-3 font-medium">更新时间</th><th className="px-3 py-3 font-medium">状态</th><th className="px-3 py-3 font-medium">来源</th><th className="px-3 py-3 text-right font-medium">操作</th>
        </tr></thead>
        <tbody className="divide-y divide-border">{entries.map((entry) => <tr key={entry.id} data-testid={`shared-library-row-${entry.id}`} className="transition-colors duration-normal hover:bg-surface-muted/60">
          <td className="px-1.5 py-3"><Checkbox aria-label={`共享资料${entry.name}不可选择`} disabled /></td>
          <td className="px-1 py-3"><ManagedItemType folder={entry.kind === "folder"} sharedFolder={entry.kind === "folder"} docType={entry.kind === "video" ? "video" : undefined} compact /></td>
          <td className="max-w-xs px-1.5 py-3"><button type="button" className="block max-w-full rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" disabled={entry.kind !== "folder"} onClick={() => entry.kind === "folder" && setParent(entry.relative_path)}><span className="flex flex-wrap items-center gap-2"><span className="break-words font-medium">{entry.name}</span><Badge variant="secondary">共享</Badge></span><span className="mt-0.5 block break-all text-ui-xs text-muted-foreground">{entry.relative_path}</span></button></td>
          <td className="whitespace-nowrap px-3 py-3 tabular-nums">{externalUpdatedAt(entry)}</td><td className="px-3 py-3 text-muted-foreground">{availabilityLabel(entry)}</td><td className="px-3 py-3">共享目录</td>
          <td className="px-3 py-3 text-right">{entry.kind === "folder" ? <Button size="sm" variant="outline" aria-label={`打开${entry.name}`} onClick={() => setParent(entry.relative_path)}>打开</Button> : <span className="text-ui-xs text-muted-foreground">只读</span>}</td>
        </tr>)}</tbody>
      </table></div>
      <ul className="divide-y divide-border border-t border-border lg:hidden" aria-label="共享目录内容">{entries.map((entry) => <li key={entry.id} data-testid={`shared-library-mobile-${entry.id}`} className="space-y-3 px-4 py-4 sm:px-5">
        <div className="flex items-start gap-2"><Checkbox className="mt-0.5" aria-label={`共享资料${entry.name}不可选择`} disabled /><ManagedItemType folder={entry.kind === "folder"} sharedFolder={entry.kind === "folder"} docType={entry.kind === "video" ? "video" : undefined} /><button type="button" className="min-w-0 flex-1 text-left" disabled={entry.kind !== "folder"} onClick={() => entry.kind === "folder" && setParent(entry.relative_path)}><span className="flex flex-wrap items-center gap-2"><span className="break-words font-medium">{entry.name}</span><Badge variant="secondary">共享</Badge></span><span className="mt-0.5 block break-all text-ui-xs text-muted-foreground">{entry.relative_path}</span></button></div>
        <dl className="grid grid-cols-[4rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-ui-sm"><dt className="text-muted-foreground">状态</dt><dd>{availabilityLabel(entry)}</dd><dt className="text-muted-foreground">更新时间</dt><dd className="tabular-nums">{externalUpdatedAt(entry)}</dd><dt className="text-muted-foreground">来源</dt><dd>共享目录</dd></dl>
      </li>)}</ul>
    </>}
  </section>;
}

export function ExternalCategoryTree({ sourceId, parent = "", level }: { sourceId: string; parent?: string; level: number }) {
  const { entries, loading, error } = useExternalEntries(sourceId, parent);
  if (loading) return <div className="px-4 py-3 text-ui-xs text-muted-foreground" role="status">正在读取共享目录…</div>;
  if (error) return <div className="px-4 py-3 text-ui-xs text-destructive" role="alert">{error}</div>;
  if (entries.length === 0) return <div className="px-4 py-3 text-ui-xs text-muted-foreground">当前目录暂无资料</div>;
  return <>{entries.map((entry) => <ExternalCategoryTreeEntry key={entry.id} sourceId={sourceId} entry={entry} level={level} />)}</>;
}

function ExternalCategoryTreeEntry({ sourceId, entry, level }: { sourceId: string; entry: ExternalMediaEntry; level: number }) {
  const [expanded, setExpanded] = useState(false);
  const folder = entry.kind === "folder";
  return <>
    <div role="treeitem" aria-level={level} aria-expanded={folder ? expanded : undefined} data-testid={`shared-tree-item-${entry.id}`} className="relative flex min-h-[3.25rem] items-center gap-2 border-b border-l-2 border-b-border border-l-transparent py-2 pl-3 pr-3 hover:bg-surface-muted/60 before:absolute before:left-0 before:top-1/2 before:w-3 before:border-t before:border-border">
      <span className="flex w-11 shrink-0 items-center gap-1">{folder ? <button type="button" aria-label={expanded ? `收起${entry.name}` : `展开${entry.name}`} onClick={() => setExpanded((value) => !value)} className="inline-flex size-6 items-center justify-center rounded-ui-sm text-muted-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{expanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}</button> : <span className="size-6" />}{folder ? expanded ? <FolderOpen className="size-4 text-info" /> : <Folder className="size-4 text-info" /> : <Film className="size-4 text-primary" />}</span>
      <span className="flex min-w-0 flex-1 items-center gap-2"><span className="inline-flex w-11 shrink-0 items-center gap-1 text-ui-xs font-medium text-info"><span className="size-2 rounded-full bg-info" />共享</span><span className="min-w-0 flex-1 break-words font-medium">{entry.name} <Badge className="ml-1" variant="secondary">共享</Badge></span><span className="hidden shrink-0 text-ui-xs text-muted-foreground sm:block">{folder ? "共享子目录" : availabilityLabel(entry)}</span></span>
    </div>
    {folder && expanded && <div role="group" className="ml-5 border-l border-border bg-surface-muted/10 sm:ml-6"><ExternalCategoryTree sourceId={sourceId} parent={entry.relative_path} level={level + 1} /></div>}
  </>;
}
