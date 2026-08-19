import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Download, FileText, Folder, FolderInput, Rocket, Send, Trash2, X } from "lucide-react";
import { adminContentApi } from "../../api/admin/content";
import type { BulkOperation, BulkOperationAction, ManagedCategory, ManagedContentItem } from "../../types";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { Input } from "../ui/input";
import { LoadingState } from "../ui/loading-state";
import { CategoryDestinationPicker } from "./CategoryDestinationPicker";

const actionLabels: Record<BulkOperationAction, string> = {
  move: "批量调整目录",
  submit: "批量提交审核",
  approve: "批量确认",
  reject: "批量退回",
  publish: "批量发布",
  download: "批量打包下载",
  delete: "批量删除文件夹",
  force_delete: "强制永久删除文件夹",
};

const statusLabels: Record<string, string> = {
  draft: "待提交", awaiting_review: "待确认", approved: "已确认", rejected: "已退回",
  publishing: "发布中", published: "已发布", publication_failed: "发布失败", superseded: "历史版本",
};

function formatSize(bytes: number) {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GiB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${bytes} B`;
}

type Props = {
  action: BulkOperationAction | null;
  selectedFolders: ManagedCategory[];
  selectedItems: ManagedContentItem[];
  categories: ManagedCategory[];
  onCreateFolder?: (parentCategoryId: string, displayName: string) => Promise<ManagedCategory>;
  onClose: () => void;
  onCompleted: () => void | Promise<void>;
};

export function ManagedContentBulkOperationDialog({
  action,
  selectedFolders,
  selectedItems,
  categories,
  onCreateFolder,
  onClose,
  onCompleted,
}: Props) {
  const [run, setRun] = useState<BulkOperation | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [itemBusy, setItemBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [targetCategoryId, setTargetCategoryId] = useState("");
  const [note, setNote] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const completedRunRef = useRef<string | null>(null);

  // Capture the opening selection so clearing the parent selection does not replace a completed result.
  useEffect(() => {
    if (!action) {
      setRun(null);
      setError(null);
      setTargetCategoryId("");
      setNote("");
      setConfirmation("");
      completedRunRef.current = null;
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    void adminContentApi.preflightBulkOperation(
      action,
      selectedFolders.map((folder) => ({ category_id: folder.id, expected_version: folder.version })),
      selectedItems.map((item) => ({ item_id: item.item_id, expected_version_id: item.version_id })),
    ).then((result) => { if (!cancelled) setRun(result); })
      .catch((loadError) => { if (!cancelled) setError(loadError instanceof Error ? loadError.message : "批量检查失败"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [action]);

  useEffect(() => {
    if (!run || !["queued", "packaging", "running"].includes(run.status)) return;
    const timer = window.setInterval(() => {
      void adminContentApi.bulkOperation(run.id, false).then(async (progress) => {
        if (["ready", "succeeded", "partial", "failed", "cancelled", "expired"].includes(progress.status)) {
          setRun(await adminContentApi.bulkOperation(progress.id));
          return;
        }
        setRun((current) => current?.id === progress.id ? {
          ...progress,
          categories: current.categories,
          items: current.items,
        } : progress);
      }).catch((pollError) => {
        setError(pollError instanceof Error ? pollError.message : "进度刷新失败");
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [run?.id, run?.status]);

  useEffect(() => {
    if (!run || action !== "force_delete" || !["succeeded", "partial", "failed"].includes(run.status)) return;
    if (completedRunRef.current === run.id) return;
    completedRunRef.current = run.id;
    void onCompleted();
  }, [action, onCompleted, run]);

  const selectedEligible = run?.items.filter((item) => item.eligible && item.selected && item.result_status === "pending") || [];
  const excludedCount = run?.items.filter((item) => !item.eligible).length || 0;
  const rootCount = run?.categories.filter((category) => category.is_root).length || 0;
  const eligibleRootCount = run?.categories.filter((category) => category.is_root && category.eligible && category.selected).length || 0;
  const archiveTooLarge = Boolean(run && action === "download" && run.total_bytes > run.max_archive_bytes);
  const progress = run && run.total_bytes > 0 ? Math.min(100, Math.round((run.processed_bytes / run.total_bytes) * 100)) : 0;
  const itemsByCategory = useMemo(() => {
    const map = new Map<string, NonNullable<BulkOperation["items"]>>();
    for (const item of run?.items || []) map.set(item.category_id, [...(map.get(item.category_id) || []), item]);
    return map;
  }, [run]);
  const renderedCategoryIds = useMemo(
    () => new Set((run?.categories || []).map((category) => category.category_id)),
    [run?.categories],
  );
  const directItemsOutsideSelectedFolders = (run?.items || []).filter(
    (item) => item.scope_source === "direct" && !renderedCategoryIds.has(item.category_id),
  );
  const disabledMoveDestinations = useMemo(() => {
    if (action !== "move") return {};
    const reasons: Record<string, string> = {};
    for (const destination of categories) {
      for (const root of selectedFolders) {
        if (destination.id === root.id || destination.full_path.startsWith(`${root.full_path} /`)) {
          reasons[destination.id] = "不能移动到所选文件夹自身或其子文件夹";
          break;
        }
        const subtreeLevels = categories
          .filter((category) => category.id === root.id || category.full_path.startsWith(`${root.full_path} /`))
          .map((category) => category.level);
        const subtreeHeight = Math.max(root.level, ...subtreeLevels) - root.level;
        if (destination.level + 1 + subtreeHeight > 4) {
          reasons[destination.id] = "移动后目录层级会超过四级";
          break;
        }
      }
    }
    return reasons;
  }, [action, categories, selectedFolders]);

  const updateSelection = async (itemIds: string[], selected: boolean) => {
    if (!run || itemIds.length === 0) return;
    setBusy(true);
    setError(null);
    try { setRun(await adminContentApi.updateBulkSelection(run.id, itemIds, selected)); }
    catch (selectionError) { setError(selectionError instanceof Error ? selectionError.message : "选择更新失败"); }
    finally { setBusy(false); }
  };

  const reviewOne = async (itemId: string, approved: boolean) => {
    if (!run || (!approved && !note.trim())) return;
    setItemBusy(itemId);
    setError(null);
    try {
      const result = await adminContentApi.reviewBulkItem(run.id, itemId, approved, note);
      setRun(result);
      if (["succeeded", "partial"].includes(result.status)) await onCompleted();
    }
    catch (reviewError) { setError(reviewError instanceof Error ? reviewError.message : "单项审核失败"); }
    finally { setItemBusy(null); }
  };

  const execute = async () => {
    if (!run || !action) return;
    setBusy(true);
    setError(null);
    try {
      const result = await adminContentApi.executeBulkOperation(run.id, {
        target_category_id: action === "move" ? targetCategoryId : undefined,
        note: note.trim() || undefined,
        confirmation: action === "force_delete" ? confirmation : undefined,
      });
      setRun(result);
      if (["succeeded", "partial"].includes(result.status)) await onCompleted();
    } catch (executeError) {
      setError(executeError instanceof Error ? executeError.message : "批量操作失败");
    } finally {
      setBusy(false);
    }
  };

  const downloadArchive = () => {
    if (!run) return;
    const anchor = document.createElement("a");
    anchor.href = adminContentApi.bulkArchiveUrl(run.id);
    anchor.download = run.archive_filename || "资料目录打包.zip";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  };

  const canExecute = Boolean(run && run.status === "awaiting_confirmation"
    && (action === "delete" || action === "force_delete" ? eligibleRootCount > 0 : action === "download" ? selectedEligible.length > 0 || rootCount > 0 : selectedEligible.length > 0)
    && (action !== "move" || targetCategoryId)
    && (action !== "reject" || note.trim())
    && (action !== "force_delete" || confirmation === run.confirmation_phrase)
    && !archiveTooLarge);

  const reviewActions = (item: BulkOperation["items"][number]) => (
    (action === "approve" || action === "reject") && item.eligible && item.result_status === "pending"
      ? <><Button size="sm" variant="outline" disabled={Boolean(itemBusy) || busy} onClick={() => void reviewOne(item.item_id, true)}><Check className="size-4" />通过</Button><Button size="sm" variant="outline" disabled={Boolean(itemBusy) || busy || !note.trim()} onClick={() => void reviewOne(item.item_id, false)}><X className="size-4" />退回</Button></>
      : null
  );

  return <Dialog open={Boolean(action)} onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <DialogContent className="flex max-h-[calc(100vh-2rem)] max-w-4xl flex-col overflow-hidden">
      <DialogHeader>
        <DialogTitle>{action ? actionLabels[action] : "批量操作"}</DialogTitle>
        <DialogDescription>系统已递归展开所选文件夹。只会处理勾选且状态符合要求的资料。</DialogDescription>
      </DialogHeader>
      {loading ? <LoadingState className="min-h-56 border-0" label="正在检查目录和资料…" /> : <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
        {run && <>
          <div className="flex flex-wrap gap-2" role="status">
            <Badge>{run.total_folders} 个文件夹</Badge>
            <Badge variant="success">可处理 {selectedEligible.length} 份</Badge>
            {excludedCount > 0 && <Badge variant="outline">不受影响 {excludedCount} 份</Badge>}
            {run.total_bytes > 0 && <Badge variant="outline">{formatSize(run.total_bytes)}</Badge>}
          </div>
          {action === "move" && <CategoryDestinationPicker
            categories={categories}
            value={targetCategoryId}
            onChange={setTargetCategoryId}
            label="目标目录"
            onCreateFolder={onCreateFolder}
            disabledCategoryReasons={disabledMoveDestinations}
          />}
          {(action === "reject" || action === "approve") && <label className="block space-y-1.5 text-ui-sm font-medium">
            <span>退回原因{action === "reject" ? "（必填）" : "（用于单项退回）"}</span>
            <textarea className="min-h-24 w-full resize-y rounded-ui-md border border-input bg-background px-3 py-2 text-ui-sm" value={note} maxLength={2000} onChange={(event) => setNote(event.target.value)} />
          </label>}
          {action === "force_delete" && <div className="space-y-2 rounded-ui-md border border-destructive/40 bg-destructive/5 p-3">
            <p className="text-ui-sm font-medium text-destructive">此操作不可恢复</p>
            <p className="text-ui-xs text-muted-foreground">请输入：{run.confirmation_phrase}</p>
            <Input aria-label="强制删除确认文字" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
          </div>}
          {run.status === "packaging" || run.status === "queued" || (run.status === "running" && action === "force_delete") ? <div className="space-y-2" aria-live="polite">
            <div className="flex items-center justify-between text-ui-sm"><span>{action === "force_delete" ? run.status === "queued" ? "等待执行永久删除" : "正在逐个清理所选目录" : run.status === "queued" ? "等待打包" : "正在生成 ZIP64 压缩包"}</span>{action === "download" && <span className="tabular-nums">{progress}%</span>}</div>
            {action === "download" && <><div className="h-2 overflow-hidden rounded-full bg-secondary"><div className="h-full bg-primary transition-[width]" style={{ width: `${progress}%` }} /></div><p className="text-ui-xs text-muted-foreground">已处理 {formatSize(run.processed_bytes)} / {formatSize(run.total_bytes)}</p></>}
          </div> : null}
          {run.status === "ready" && <div className="flex items-center justify-between gap-3 rounded-ui-md border border-success/40 bg-success/5 p-3"><p className="text-ui-sm">压缩包已准备完成，浏览器将接管实际下载进度。</p><Button onClick={downloadArchive}><Download className="size-4" />下载压缩包</Button></div>}
          {archiveTooLarge && <p className="rounded-ui-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-ui-sm text-destructive" role="alert">当前勾选内容为 {formatSize(run.total_bytes)}，超过单次 {formatSize(run.max_archive_bytes)} 上限。请取消部分资料后再打包。</p>}
          {run.status === "failed" && run.error_summary && <p className="rounded-ui-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-ui-sm text-destructive" role="alert">{run.error_summary}</p>}
          {["succeeded", "partial", "failed", "cancelled", "expired"].includes(run.status) && (action !== "download" || run.status === "cancelled" || run.status === "expired") && <div className="rounded-ui-md border border-border bg-surface-muted/40 px-3 py-2 text-ui-sm" role="status">
            {run.status === "succeeded" ? "批量操作已完成" : run.status === "partial" ? `批量操作部分完成，${run.failed_files} 项失败` : run.status === "cancelled" ? "批量任务已取消" : run.status === "expired" ? "压缩包已过期，请重新打包" : `批量操作失败，${run.failed_files} 项未完成`}
          </div>}
          <div className="flex items-center justify-between gap-3">
            <p className="text-ui-sm font-medium">影响范围</p>
            {!(["delete", "force_delete"].includes(action || "")) && <div className="flex gap-2"><Button size="sm" variant="ghost" disabled={busy} onClick={() => void updateSelection(run.items.filter((item) => item.eligible && item.result_status === "pending").map((item) => item.item_id), true)}>全选可操作项</Button><Button size="sm" variant="ghost" disabled={busy} onClick={() => void updateSelection(selectedEligible.map((item) => item.item_id), false)}>全部取消</Button></div>}
          </div>
          <div className="max-h-[24rem] overflow-y-auto border-y border-border" aria-label="批量操作影响文件树">
            {run.categories.map((category) => <div key={category.category_id}>
              <div className="flex min-h-11 items-center gap-2 bg-surface-muted/40 px-3 py-2 text-ui-sm">
                <Folder className="size-4 shrink-0 text-primary" />
                <span className="min-w-0 flex-1 break-words font-medium">{category.full_path}</span>
                {category.is_root && !category.eligible && <span className="text-ui-xs text-destructive">{category.reason}</span>}
              </div>
              {(itemsByCategory.get(category.category_id) || []).map((item) => <div key={item.item_id} className="grid gap-2 border-t border-border/70 px-3 py-2.5 pl-8 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-center">
                {!(["delete", "force_delete"].includes(action || "")) ? <Checkbox aria-label={`选择${item.title}`} checked={item.selected} disabled={!item.eligible || busy || item.result_status !== "pending" || (action === "move" && item.scope_source === "category")} title={action === "move" && item.scope_source === "category" ? "资料将随所选文件夹一并移动" : undefined} onChange={() => void updateSelection([item.item_id], !item.selected)} /> : <FileText className="size-4 text-muted-foreground" />}
                <div className="min-w-0"><p className="break-words text-ui-sm font-medium">{item.title}</p><p className="break-all text-ui-xs text-muted-foreground">{item.original_filename} · {statusLabels[item.lifecycle_status] || item.lifecycle_status}</p>{(item.reason || item.result_message) && <p className={`mt-1 text-ui-xs ${item.eligible ? "text-destructive" : "text-muted-foreground"}`}>{item.result_message || item.reason}</p>}</div>
                <div className="flex flex-wrap items-center justify-end gap-2">
                  {item.result_status === "succeeded" ? <Badge variant="success">已处理</Badge> : item.eligible ? <Badge variant="outline">可处理</Badge> : <Badge variant="secondary">不处理</Badge>}
                  {reviewActions(item)}
                </div>
              </div>)}
            </div>)}
            {directItemsOutsideSelectedFolders.length > 0 && <div>
              <div className="flex min-h-11 items-center gap-2 bg-surface-muted/40 px-3 py-2 text-ui-sm">
                <Folder className="size-4 shrink-0 text-primary" />
                <span className="min-w-0 flex-1 break-words font-medium">散选资料</span>
              </div>
              {directItemsOutsideSelectedFolders.map((item) => <div key={item.item_id} className="grid gap-2 border-t border-border/70 px-3 py-2.5 pl-8 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-center">
                <Checkbox aria-label={`选择${item.title}`} checked={item.selected} disabled={!item.eligible || busy || item.result_status !== "pending"} onChange={() => void updateSelection([item.item_id], !item.selected)} />
                <div className="min-w-0"><p className="break-words text-ui-sm font-medium">{item.title}</p><p className="break-all text-ui-xs text-muted-foreground">{item.category_path} / {item.original_filename} · {statusLabels[item.lifecycle_status] || item.lifecycle_status}</p>{(item.reason || item.result_message) && <p className={`mt-1 text-ui-xs ${item.eligible ? "text-destructive" : "text-muted-foreground"}`}>{item.result_message || item.reason}</p>}</div>
                <div className="flex flex-wrap items-center justify-end gap-2">{item.result_status === "succeeded" ? <Badge variant="success">已处理</Badge> : item.eligible ? <Badge variant="outline">可处理</Badge> : <Badge variant="secondary">不处理</Badge>}{reviewActions(item)}</div>
              </div>)}
            </div>}
          </div>
        </>}
        {error && <p className="rounded-ui-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-ui-sm text-destructive" role="alert">{error}</p>}
      </div>}
      <DialogFooter>
        <Button variant="outline" disabled={busy} onClick={onClose}>关闭</Button>
        {run && (["queued", "packaging"].includes(run.status) || (run.status === "running" && action === "force_delete")) && <Button variant="outline" disabled={busy} onClick={() => void adminContentApi.cancelBulkOperation(run.id).then(setRun)}>取消任务</Button>}
        {run?.status === "awaiting_confirmation" && <Button variant={action === "force_delete" ? "destructive" : "default"} disabled={!canExecute || busy} onClick={() => void execute()}>
          {action === "move" ? <FolderInput className="size-4" /> : action === "submit" ? <Send className="size-4" /> : action === "publish" ? <Rocket className="size-4" /> : action === "download" ? <Download className="size-4" /> : action === "delete" || action === "force_delete" ? <Trash2 className="size-4" /> : <Check className="size-4" />}
          {busy ? "处理中…" : action === "download" ? `开始打包（${rootCount} 个目录，${selectedEligible.length} 份资料）` : `确认执行（${action === "delete" || action === "force_delete" ? eligibleRootCount : selectedEligible.length}）`}
        </Button>}
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
