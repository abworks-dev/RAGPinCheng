import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, ChevronLeft, ChevronRight, Film, Folder, FolderOpen } from "lucide-react";
import { api } from "../../api/client";
import type { ExternalMediaEntry, ManagedContentItem } from "../../types";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { EmptyState } from "../ui/empty-state";
import { LoadingState } from "../ui/loading-state";
import { ManagedItemType } from "./ManagedItemType";

type SharedSortKey = "docType" | "title" | "updatedAt" | "status" | "source";

type BrowserProps = {
  sourceId: string;
  categoryId?: string;
  query?: string;
  lifecycleStatus?: string;
  sourceOrigin?: string;
  docType?: "pdf" | "doc" | "docx" | "xls" | "xlsx" | "ppt" | "pptx" | "xmind" | "markdown" | "transcript" | "other";
  title?: string;
  managedItems?: ManagedContentItem[];
  selectedVersionIds?: string[];
  onToggleItem?: (item: ManagedContentItem) => void;
  onManagedItemsLoaded?: (items: ManagedContentItem[]) => void;
  renderItemStatus?: (item: ManagedContentItem) => ReactNode;
  renderItemActions?: (item: ManagedContentItem) => ReactNode;
  renderFolderActions?: (entry: ExternalMediaEntry, open: () => void) => ReactNode;
};

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

function managedUpdatedAt(timestamp: number) {
  const date = new Date(timestamp * 1_000);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

const lifecycleLabel: Record<string, string> = {
  awaiting_transcription: "待转录", transcribing: "转录中", transcription_failed: "转录失败",
  transcript_ready: "转录稿待审核", transcript_awaiting_review: "转录稿待审核",
  transcript_rejected: "审核退回", transcript_approved: "待发布", publishing: "发布中",
  published: "已发布", publication_failed: "发布失败",
};

function lifecycleVariant(status: string) {
  if (status === "published") return "success" as const;
  if (status.includes("failed")) return "destructive" as const;
  if (["transcript_approved", "publishing"].includes(status)) return "warning" as const;
  return "secondary" as const;
}

function availabilityLabel(entry: ExternalMediaEntry) {
  if (entry.kind === "folder") return "共享只读";
  if (entry.availability === "missing") return "远程文件缺失";
  if (entry.availability === "superseded") return "已更新";
  return "可用 · 只读";
}

export function ExternalFolderBrowser({
  sourceId,
  categoryId,
  query,
  lifecycleStatus,
  sourceOrigin,
  docType,
  title = "共享目录",
  managedItems = [],
  selectedVersionIds = [],
  onToggleItem,
  onManagedItemsLoaded,
  renderItemStatus,
  renderItemActions,
  renderFolderActions,
}: BrowserProps) {
  const [parent, setParent] = useState("");
  const [sort, setSort] = useState<{ key: SharedSortKey; direction: "asc" | "desc" } | null>(null);
  const { entries, loading, error } = useExternalEntries(sourceId, parent);
  const parentUp = parent.includes("/") ? parent.slice(0, parent.lastIndexOf("/")) : "";
  const [allManagedItems, setAllManagedItems] = useState<ManagedContentItem[]>(managedItems);
  useEffect(() => {
    let cancelled = false;
    if (!categoryId) {
      setAllManagedItems(managedItems);
      return () => { cancelled = true; };
    }
    void api.managedContentItemsAll({
      category_id: categoryId,
      query: query || undefined,
      lifecycle_status: lifecycleStatus || undefined,
      source_origin: sourceOrigin || undefined,
      doc_type: docType,
    }).then((rows) => {
      if (!cancelled) {
        setAllManagedItems(rows);
        onManagedItemsLoaded?.(rows);
      }
    }).catch(() => {
      if (!cancelled) setAllManagedItems(managedItems);
    });
    return () => { cancelled = true; };
  }, [categoryId, docType, lifecycleStatus, managedItems, onManagedItemsLoaded, query, sourceOrigin]);
  const managedByMediaId = useMemo(
    () => new Map(allManagedItems.filter((item) => item.media_id).map((item) => [item.media_id!, item])),
    [allManagedItems],
  );
  useEffect(() => { setParent(""); }, [sourceId]);
  const toggleSort = (key: SharedSortKey) =>
    setSort((current) =>
      current?.key === key
        ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
        : { key, direction: "asc" },
    );
  const sortIcon = (key: SharedSortKey) =>
    sort?.key !== key ? (
      <ArrowUpDown className="size-3.5" />
    ) : sort.direction === "asc" ? (
      <ArrowUp className="size-3.5" />
    ) : (
      <ArrowDown className="size-3.5" />
    );
  const entryManagedItem = (entry: ExternalMediaEntry) =>
    entry.media_id ? managedByMediaId.get(entry.media_id) : undefined;
  const entrySortTime = (entry: ExternalMediaEntry) => {
    const managedItem = entryManagedItem(entry);
    return managedItem
      ? ((managedItem.updated_at || 0) * 1_000)
      : (entry.modified_ns ? entry.modified_ns / 1_000_000 : 0);
  };
  const entrySortStatus = (entry: ExternalMediaEntry) => {
    const managedItem = entryManagedItem(entry);
    return managedItem
      ? (managedItem.lifecycle_status || "")
      : (entry.lifecycle_status || entry.availability || "");
  };
  const visibleEntries = useMemo(() => {
    const base = entries.filter((entry) => entry.kind === "folder" || managedByMediaId.has(entry.media_id || ""));
    if (!sort) return base;
    const direction = sort.direction === "asc" ? 1 : -1;
    const compare = (left: ExternalMediaEntry, right: ExternalMediaEntry) => {
      switch (sort.key) {
        case "docType": {
          const leftType = left.kind === "folder" ? "文件夹" : left.kind === "video" ? "视频" : left.kind;
          const rightType = right.kind === "folder" ? "文件夹" : right.kind === "video" ? "视频" : right.kind;
          return leftType.localeCompare(rightType, "zh-CN");
        }
        case "title":
          return left.name.localeCompare(right.name, "zh-CN", { numeric: true, sensitivity: "base" });
        case "updatedAt":
          return entrySortTime(left) - entrySortTime(right);
        case "status":
          return entrySortStatus(left).localeCompare(entrySortStatus(right), "zh-CN");
        default:
          return 0;
      }
    };
    const sortedFolders = base.filter((entry) => entry.kind === "folder").sort((a, b) => compare(a, b) * direction);
    const sortedVideos = base.filter((entry) => entry.kind !== "folder").sort((a, b) => compare(a, b) * direction);
    return [...sortedFolders, ...sortedVideos];
  }, [entries, managedByMediaId, sort]);
  const selectableRows = visibleEntries.filter((entry) => Boolean(entryManagedItem(entry) && onToggleItem));
  const allSelected =
    selectableRows.length > 0 &&
    selectableRows.every((entry) => {
      const managedItem = entryManagedItem(entry)!;
      return selectedVersionIds.includes(managedItem.version_id);
    });
  const toggleAll = () => {
    if (!onToggleItem) return;
    selectableRows.forEach((entry) => {
      const managedItem = entryManagedItem(entry)!;
      if (allSelected) {
        if (selectedVersionIds.includes(managedItem.version_id)) onToggleItem(managedItem);
      } else if (!selectedVersionIds.includes(managedItem.version_id)) {
        onToggleItem(managedItem);
      }
    });
  };
  const openEntry = (entry: ExternalMediaEntry) => setParent(entry.relative_path);
  return <section aria-label={title}>
    <div className="flex items-center gap-2 border-b border-border bg-surface-muted/40 px-4 py-2 text-ui-xs sm:px-5">
      <Button size="sm" variant="ghost" disabled={!parent} onClick={() => setParent(parentUp)}><ChevronLeft className="size-4" />上一级</Button>
      <span className="min-w-0 flex-1 truncate text-muted-foreground" title={parent || "根目录"}>{parent || "根目录"}</span>
      <Badge variant="secondary">共享</Badge>
    </div>
    {loading ? <LoadingState className="min-h-48" label="正在读取共享目录…" /> : error ? <p className="p-4 text-ui-sm text-destructive" role="alert">{error}</p> : visibleEntries.length === 0 ? <EmptyState title="当前目录暂无资料" description="共享目录扫描后会在这里显示直属文件夹和文件。" /> : <>
      <div className="hidden overflow-x-auto border-t border-border lg:block"><table className="w-full min-w-[72rem] text-ui-sm">
        <thead className="border-b border-border bg-surface-muted text-left text-muted-foreground"><tr>
          <th className="w-8 px-1.5 py-3"><Checkbox aria-label="选择当前页资料" checked={allSelected} onChange={toggleAll} disabled={selectableRows.length === 0} /></th>
          {([
            ["docType", "类型"],
            ["title", "资料"],
            ["updatedAt", "更新时间"],
            ["status", "状态"],
            ["source", "来源"],
          ] as [SharedSortKey, string][]).map(([key, label]) => (
            <th
              key={key}
              aria-sort={sort?.key === key ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
              className={key === "docType" ? "w-16 px-1 py-3 text-center font-medium" : key === "title" ? "px-1.5 py-3 font-medium" : "px-3 py-3 font-medium"}
            >
              <button
                type="button"
                className="inline-flex items-center gap-1 rounded px-1 py-0.5 hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => toggleSort(key)}
              >
                {label}
                {sortIcon(key)}
              </button>
            </th>
          ))}
          <th className="px-3 py-3 text-right font-medium">操作</th>
        </tr></thead>
        <tbody className="divide-y divide-border">{visibleEntries.map((entry) => {
          const managedItem = entryManagedItem(entry);
          return <tr key={entry.id} data-testid={`shared-library-row-${entry.id}`} className={`transition-colors duration-normal hover:bg-surface-muted/60 ${entry.kind === "folder" ? "cursor-pointer" : ""}`} onClick={() => entry.kind === "folder" && openEntry(entry)}>
          <td className="px-1.5 py-3"><Checkbox aria-label={managedItem ? `选择${managedItem.title}` : `共享资料${entry.name}不可选择`} checked={Boolean(managedItem && selectedVersionIds.includes(managedItem.version_id))} disabled={!managedItem || !onToggleItem} onChange={() => managedItem && onToggleItem?.(managedItem)} /></td>
          <td className="px-1 py-3"><ManagedItemType folder={entry.kind === "folder"} sharedFolder={entry.kind === "folder"} docType={entry.kind === "video" ? "video" : undefined} compact /></td>
          <td className="max-w-xs px-1.5 py-3"><button type="button" className="block max-w-full rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" disabled={entry.kind !== "folder"} onClick={() => entry.kind === "folder" && openEntry(entry)}><span className="flex flex-wrap items-center gap-2"><span className="break-words font-medium">{managedItem?.title || entry.name}</span><Badge variant="secondary">共享</Badge></span><span className="mt-0.5 block break-all text-ui-xs text-muted-foreground">{entry.relative_path}</span></button></td>
          <td className="whitespace-nowrap px-3 py-3 tabular-nums">{managedItem ? managedUpdatedAt(managedItem.updated_at) : externalUpdatedAt(entry)}</td><td className="px-3 py-3">{managedItem && renderItemStatus ? renderItemStatus(managedItem) : <span className="text-muted-foreground">{availabilityLabel(entry)}</span>}</td><td className="px-3 py-3">共享目录</td>
          <td className="px-3 py-3 text-right">{entry.kind === "folder" ? renderFolderActions ? renderFolderActions(entry, () => openEntry(entry)) : <Button size="sm" variant="outline" aria-label={`打开${entry.name}`} onClick={() => openEntry(entry)}>打开</Button> : managedItem && renderItemActions ? renderItemActions(managedItem) : <span className="text-ui-xs text-muted-foreground">资料状态加载中</span>}</td>
        </tr>;})}</tbody>
      </table></div>
      <ul className="divide-y divide-border border-t border-border lg:hidden" aria-label="共享目录内容">{visibleEntries.map((entry) => {
        const managedItem = entryManagedItem(entry);
        return <li key={entry.id} data-testid={`shared-library-mobile-${entry.id}`} className="space-y-3 px-4 py-4 sm:px-5">
        <div className="flex items-start gap-2"><Checkbox className="mt-0.5" aria-label={managedItem ? `选择${managedItem.title}` : `共享资料${entry.name}不可选择`} checked={Boolean(managedItem && selectedVersionIds.includes(managedItem.version_id))} disabled={!managedItem || !onToggleItem} onChange={() => managedItem && onToggleItem?.(managedItem)} /><ManagedItemType folder={entry.kind === "folder"} sharedFolder={entry.kind === "folder"} docType={entry.kind === "video" ? "video" : undefined} /><button type="button" className="min-w-0 flex-1 text-left" disabled={entry.kind !== "folder"} onClick={() => entry.kind === "folder" && openEntry(entry)}><span className="flex flex-wrap items-center gap-2"><span className="break-words font-medium">{managedItem?.title || entry.name}</span><Badge variant="secondary">共享</Badge></span><span className="mt-0.5 block break-all text-ui-xs text-muted-foreground">{entry.relative_path}</span></button></div>
        <dl className="grid grid-cols-[4rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-ui-sm"><dt className="text-muted-foreground">状态</dt><dd>{managedItem && renderItemStatus ? renderItemStatus(managedItem) : availabilityLabel(entry)}</dd><dt className="text-muted-foreground">更新时间</dt><dd className="tabular-nums">{externalUpdatedAt(entry)}</dd><dt className="text-muted-foreground">来源</dt><dd>共享目录</dd></dl>
        {entry.kind === "folder" ? (renderFolderActions && <div className="flex justify-end">{renderFolderActions(entry, () => openEntry(entry))}</div>) : (managedItem && renderItemActions && <div className="flex justify-end">{renderItemActions(managedItem)}</div>)}
      </li>;})}</ul>
    </>}
  </section>;
}

export function ExternalCategoryTree({ sourceId, parent = "", level, foldersOnly = false }: { sourceId: string; parent?: string; level: number; foldersOnly?: boolean }) {
  const { entries, loading, error } = useExternalEntries(sourceId, parent);
  const visibleEntries = foldersOnly ? entries.filter((entry) => entry.kind === "folder") : entries;
  if (loading) return <div className="px-4 py-3 text-ui-xs text-muted-foreground" role="status">正在读取共享目录…</div>;
  if (error) return <div className="px-4 py-3 text-ui-xs text-destructive" role="alert">{error}</div>;
  if (visibleEntries.length === 0) return <div className="px-4 py-3 text-ui-xs text-muted-foreground">当前目录暂无子文件夹</div>;
  return <>{visibleEntries.map((entry) => <ExternalCategoryTreeEntry key={entry.id} sourceId={sourceId} entry={entry} level={level} foldersOnly={foldersOnly} />)}</>;
}

function ExternalCategoryTreeEntry({ sourceId, entry, level, foldersOnly }: { sourceId: string; entry: ExternalMediaEntry; level: number; foldersOnly: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const folder = entry.kind === "folder";
  return <>
    <div role="treeitem" aria-level={level} aria-expanded={folder ? expanded : undefined} data-testid={`shared-tree-item-${entry.id}`} className="relative flex min-h-[3.25rem] items-center gap-2 border-b border-l-2 border-b-border border-l-transparent py-2 pl-3 pr-3 hover:bg-surface-muted/60 before:absolute before:left-0 before:top-1/2 before:w-3 before:border-t before:border-border">
      <span className="flex w-11 shrink-0 items-center gap-1">{folder ? <button type="button" aria-label={expanded ? `收起${entry.name}` : `展开${entry.name}`} onClick={() => setExpanded((value) => !value)} className="inline-flex size-6 items-center justify-center rounded-ui-sm text-muted-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{expanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}</button> : <span className="size-6" />}{folder ? expanded ? <FolderOpen className="size-4 text-info" /> : <Folder className="size-4 text-info" /> : <Film className="size-4 text-primary" />}</span>
      <span className="flex min-w-0 flex-1 items-center gap-2"><span className="inline-flex w-11 shrink-0 items-center gap-1 text-ui-xs font-medium text-info"><span className="size-2 rounded-full bg-info" />共享</span><span className="min-w-0 flex-1 break-words font-medium">{entry.name} <Badge className="ml-1" variant="secondary">共享</Badge></span>{!folder && entry.lifecycle_status ? <Badge variant={lifecycleVariant(entry.lifecycle_status)}>{lifecycleLabel[entry.lifecycle_status] || entry.lifecycle_status}</Badge> : <span className="hidden shrink-0 text-ui-xs text-muted-foreground sm:block">{folder ? "共享子目录" : availabilityLabel(entry)}</span>}</span>
    </div>
    {folder && expanded && <div role="group" className="ml-5 border-l border-border bg-surface-muted/10 sm:ml-6"><ExternalCategoryTree sourceId={sourceId} parent={entry.relative_path} level={level + 1} foldersOnly={foldersOnly} /></div>}
  </>;
}