import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArchiveRestore, ArrowDown, ArrowUp, ArrowUpDown, Check, ChevronRight, Download, Eye, FileText, Folder, FolderPlus, Move, RefreshCw, Rocket, Search, Send, Trash2, Upload, X } from "lucide-react";
import { api } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button, buttonVariants } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { Input } from "../../components/ui/input";
import { LoadingState } from "../../components/ui/loading-state";
import { Select } from "../../components/ui/select";
import { toast } from "../../components/ui/toast";
import { useAuth } from "../../context/AuthContext";
import { usePdfPreview } from "../../hooks/usePdfPreview";
import type { BulkManagedContentResult, ContentPermission, FolderRequest, ManagedCategory, ManagedContentItem, ManagedUploadResponse } from "../../types";
import { formatAdminDate } from "./admin-formatters";

const PAGE_SIZE = 25;
const PAGE_SIZE_OPTIONS = [25, 50, 100];
const BULK_LIMIT = 20;
type SortKey = "title" | "category" | "status" | "source";
type SortDirection = "asc" | "desc";

const statusLabel: Record<string, string> = {
  draft: "待提交", awaiting_review: "待确认", approved: "已确认", rejected: "已退回",
  publishing: "发布中", published: "已发布", publication_failed: "发布失败", superseded: "历史版本",
};
const sourceLabel: Record<string, string> = {
  web: "网页上传", server: "后台导入", legacy: "历史迁移", transcription: "视频转写",
};

function statusVariant(status: string) {
  if (status === "published") return "success" as const;
  if (status.includes("failed") || status === "rejected") return "destructive" as const;
  if (status === "awaiting_review" || status === "publishing") return "warning" as const;
  return "secondary" as const;
}

function PublicationFailure({ item }: { item: ManagedContentItem }) {
  const failure = item.publication_failure;
  if (!failure) return null;
  return <div className="mt-2 max-w-md space-y-1 text-ui-xs text-destructive" role="alert"><p className="break-words">{failure.message}</p><p className="break-words text-muted-foreground">{failure.recommended_action}</p><p className="break-words text-muted-foreground">{failure.retryable ? "可以重新发布" : "按原失败原因直接重试通常不会成功；系统或文件处理后可重新发布"}{item.publication_attempt_count > 1 ? ` · 共尝试 ${item.publication_attempt_count} 次` : ""}</p></div>;
}

type BulkAction = "approve" | "reject" | "publish";

export function AdminManagedContentPage() {
  const { state } = useAuth();
  const { open: openDocumentPreview, state: previewState } = usePdfPreview();
  const permissions = state.status === "authed" ? state.user.content_permissions || [] : [];
  const can = (permission: ContentPermission) => state.status === "authed" && (state.user.role === "admin" || permissions.includes(permission));
  const [items, setItems] = useState<ManagedContentItem[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [categories, setCategories] = useState<ManagedCategory[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [currentFolderId, setCurrentFolderId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [pendingUploadFiles, setPendingUploadFiles] = useState<File[]>([]);
  const [pendingUploadFolderId, setPendingUploadFolderId] = useState("");
  const [uploadResults, setUploadResults] = useState<ManagedUploadResponse["entries"]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [bulkAction, setBulkAction] = useState<BulkAction | null>(null);
  const [bulkFailures, setBulkFailures] = useState<Array<BulkManagedContentResult & { title: string }>>([]);
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [detail, setDetail] = useState<ManagedContentItem | null>(null);
  const [sort, setSort] = useState<{ key: SortKey; direction: SortDirection } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ManagedContentItem | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [view, setView] = useState<"library" | "trash">("library");
  const [trashItems, setTrashItems] = useState<ManagedContentItem[]>([]);
  const [trashTotal, setTrashTotal] = useState(0);
  const [trashLoading, setTrashLoading] = useState(false);
  const [restoreTarget, setRestoreTarget] = useState<ManagedContentItem | null>(null);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [moveTarget, setMoveTarget] = useState<ManagedContentItem | null>(null);
  const [moveFolderId, setMoveFolderId] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const [listDropActive, setListDropActive] = useState(false);
  const [draggedItem, setDraggedItem] = useState<ManagedContentItem | null>(null);
  const [folderRequests, setFolderRequests] = useState<FolderRequest[]>([]);
  const [requestFolderOpen, setRequestFolderOpen] = useState(false);
  const [requestFolderName, setRequestFolderName] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(queryInput.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [queryInput]);
  useEffect(() => { setPage(0); setSelected([]); }, [query, categoryFilter, currentFolderId, statusFilter, sourceFilter, pageSize]);

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true);
    else if (!currentFolderId) setLoading(true);
    setError(null);
    try {
      const [capabilities, categoryRows, listing] = await Promise.all([
        api.managedContentCapabilities(), api.managedCategories(), api.managedContentItems({
          query: query || undefined,
          category_id: query ? categoryFilter || undefined : currentFolderId || categoryFilter || undefined,
          lifecycle_status: statusFilter || undefined,
          source_origin: sourceFilter || undefined,
          limit: pageSize,
          offset: page * pageSize,
        }),
      ]);
      setEnabled(capabilities.enabled);
      setCategories(categoryRows);
      setItems(currentFolderId ? listing.items : []);
      setTotal(currentFolderId ? listing.total : 0);
      setCounts(currentFolderId ? listing.status_counts : {});
      setCurrentFolderId((current) => current && categoryRows.some((row) => row.id === current) ? current : "");
      setSelected((current) => current.filter((id) => listing.items.some((item) => item.version_id === id)));
      if (can("review") || can("manage_categories")) {
        setFolderRequests(await api.managedFolderRequests("pending"));
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "资料加载失败");
    } finally {
      setLoading(false); setRefreshing(false);
    }
  }, [categoryFilter, currentFolderId, page, pageSize, query, sourceFilter, statusFilter]);

  useEffect(() => { void load(); }, [load]);

  const loadTrash = useCallback(async () => {
    if (!(can("review") || can("publish"))) return;
    setTrashLoading(true); setError(null);
    try {
      const listing = await api.managedContentTrash({ query: query || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE });
      setTrashItems(listing.items); setTrashTotal(listing.total);
    } catch (trashFailure) {
      setError(trashFailure instanceof Error ? trashFailure.message : "回收站加载失败");
    } finally { setTrashLoading(false); }
  }, [page, query]);

  useEffect(() => { if (view === "trash") void loadTrash(); }, [loadTrash, view]);

  const upload = async (targetFolderId = currentFolderId, uploadFiles = files) => {
    setUploading(true); setUploadResults([]);
    try {
      const result = await api.uploadManagedContent(uploadFiles, targetFolderId);
      setUploadResults(result.entries);
      const accepted = result.entries.filter((entry) => entry.status === "accepted").length;
      const skipped = result.entries.length - accepted;
      toast.success(skipped ? `已接收 ${accepted} 个文件，跳过 ${skipped} 个` : `已接收 ${accepted} 个文件`);
      setFiles([]); if (fileInputRef.current) fileInputRef.current.value = "";
      await load(true);
      return true;
    } catch (uploadError) { toast.error(uploadError instanceof Error ? uploadError.message : "上传失败"); }
    finally { setUploading(false); }
    return false;
  };

  const prepareFolderUpload = (incoming: File[]) => {
    const supported = incoming.filter((file) => /\.(pdf|md|docx|xlsx|pptx)$/i.test(file.name));
    if (!supported.length || !currentFolderId) {
      if (incoming.length) toast.error("拖入的文件没有可上传的支持格式");
      return;
    }
    setPendingUploadFiles(supported);
    setPendingUploadFolderId(currentFolderId);
    setListDropActive(false);
  };

  const confirmFolderUpload = async () => {
    if (!pendingUploadFiles.length || !pendingUploadFolderId) return;
    const targetFolderId = pendingUploadFolderId;
    if (await upload(targetFolderId, pendingUploadFiles)) {
      setPendingUploadFiles([]);
      setPendingUploadFolderId("");
    }
  };

  const currentFolder = categories.find((category) => category.id === currentFolderId) || null;
  const rootFolders = categories.filter((category) => category.parent_id === null && category.is_active);
  const childFolders = categories.filter((category) => category.parent_id === (currentFolderId || null) && category.is_active);
  const breadcrumbs = useMemo(() => {
    const result: ManagedCategory[] = [];
    let cursor = currentFolder;
    while (cursor) {
      result.unshift(cursor);
      cursor = categories.find((category) => category.id === cursor?.parent_id) || null;
    }
    return result;
  }, [categories, currentFolder]);
  const currentRootFolderId = breadcrumbs[0]?.id || "";
  const sortedItems = useMemo(() => {
    if (!sort) return items;
    const value = (item: ManagedContentItem) => ({
      title: item.title,
      category: item.category_path || item.category_label,
      status: statusLabel[item.lifecycle_status] || item.lifecycle_status,
      source: sourceLabel[item.source_origin] || item.source_origin,
    })[sort.key];
    return [...items].sort((left, right) => {
      const comparison = value(left).localeCompare(value(right), "zh-CN", { numeric: true, sensitivity: "base" });
      return sort.direction === "asc" ? comparison : -comparison;
    });
  }, [items, sort]);
  const toggleSort = (key: SortKey) => setSort((current) => current?.key === key
    ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
    : { key, direction: "asc" });
  const sortIcon = (key: SortKey) => sort?.key !== key ? <ArrowUpDown className="size-3.5" /> : sort.direction === "asc" ? <ArrowUp className="size-3.5" /> : <ArrowDown className="size-3.5" />;

  const createFolder = async () => {
    if (!currentFolder || !newFolderName.trim()) return;
    setBusyAction("new-folder");
    try {
      const siblingNumber = childFolders.length + 1;
      await api.createManagedCategory({
        parent_id: currentFolder.id,
        display_code: String(siblingNumber).padStart(2, "0"),
        display_name: newFolderName.trim(),
        sort_order: siblingNumber * 10,
      });
      setNewFolderName(""); setNewFolderOpen(false); toast.success("文件夹已创建"); await load(true);
    } catch (folderError) { toast.error(folderError instanceof Error ? folderError.message : "创建文件夹失败"); }
    finally { setBusyAction(null); }
  };

  const moveContent = async () => {
    if (!moveTarget || !moveFolderId) return;
    setBusyAction(`${moveTarget.version_id}:move`);
    try {
      await api.moveManagedContent(moveTarget.item_id, moveFolderId, moveTarget.version_id);
      toast.success(`已移动“${moveTarget.title}”`); setMoveTarget(null); await load(true);
    } catch (moveError) { toast.error(moveError instanceof Error ? moveError.message : "移动资料失败"); }
    finally { setBusyAction(null); }
  };

  const moveItemTo = async (item: ManagedContentItem, targetFolderId: string) => {
    if (item.category_id === targetFolderId) return;
    setBusyAction(`${item.version_id}:move`);
    try {
      await api.moveManagedContent(item.item_id, targetFolderId, item.version_id);
      toast.success(`已移动“${item.title}”`); setDraggedItem(null); await load(true);
    } catch (moveError) { toast.error(moveError instanceof Error ? moveError.message : "移动资料失败"); }
    finally { setBusyAction(null); }
  };

  const requestFolder = async () => {
    if (!currentFolder || !requestFolderName.trim()) return;
    setBusyAction("request-folder");
    try {
      await api.createFolderRequest(currentFolder.id, requestFolderName.trim());
      setRequestFolderName(""); setRequestFolderOpen(false); toast.success("目录申请已提交");
    } catch (requestError) { toast.error(requestError instanceof Error ? requestError.message : "提交目录申请失败"); }
    finally { setBusyAction(null); }
  };

  const reviewFolder = async (request: FolderRequest, approved: boolean) => {
    setBusyAction(`folder-request:${request.id}`);
    try {
      await api.reviewFolderRequest(request.id, approved);
      toast.success(approved ? "目录申请已批准" : "目录申请已退回"); await load(true);
    } catch (reviewError) { toast.error(reviewError instanceof Error ? reviewError.message : "处理目录申请失败"); }
    finally { setBusyAction(null); }
  };

  const acceptFiles = (incoming: File[]) => {
    const supported = incoming.filter((file) => /\.(pdf|md|docx|xlsx|pptx)$/i.test(file.name));
    setFiles(supported); setUploadResults([]);
    if (supported.length !== incoming.length) toast.error("已忽略不支持的文件格式");
  };

  const openUploadDialog = () => {
    setUploadResults([]);
    setFiles([]);
    setDragActive(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
    setUploadDialogOpen(true);
  };

  const closeUploadDialog = () => {
    if (uploading) return;
    setUploadDialogOpen(false);
    setFiles([]);
    setDragActive(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const confirmDialogUpload = async () => {
    if (!files.length || !currentFolderId) return;
    const targetFolderId = currentFolderId;
    if (await upload(targetFolderId, files)) setUploadDialogOpen(false);
  };

  const act = async (item: ManagedContentItem, action: string, operation: () => Promise<unknown>, success: string) => {
    setBusyAction(`${item.version_id}:${action}`);
    try { await operation(); toast.success(success); await load(true); }
    catch (actionError) { toast.error(actionError instanceof Error ? actionError.message : "操作失败"); }
    finally { setBusyAction(null); }
  };

  const deleteContent = async () => {
    if (!deleteTarget) return;
    const target = deleteTarget;
    setBusyAction(`${target.version_id}:delete`);
    setDeleteError(null);
    try {
      await api.deleteManagedContent(target.item_id, target.version_id);
      setSelected((current) => current.filter((id) => id !== target.version_id));
      setDeleteTarget(null);
      toast.success(`已将“${target.title}”移至回收站`);
      await load(true);
    } catch (deleteFailure) {
      setDeleteError(deleteFailure instanceof Error ? deleteFailure.message : "移入回收站失败");
    } finally {
      setBusyAction(null);
    }
  };

  const restoreContent = async () => {
    if (!restoreTarget) return;
    const target = restoreTarget;
    setBusyAction(`${target.version_id}:restore`); setRestoreError(null);
    try {
      await api.restoreManagedContent(target.item_id, target.version_id);
      setRestoreTarget(null);
      toast.success(`已恢复“${target.title}”`);
      await loadTrash();
    } catch (restoreFailure) {
      setRestoreError(restoreFailure instanceof Error ? restoreFailure.message : "恢复资料失败");
    } finally { setBusyAction(null); }
  };

  const eligibleSelected = useMemo(() => {
    const allowed = bulkAction === "publish" ? new Set(["approved", "publication_failed"]) : new Set(["awaiting_review"]);
    return items.filter((item) => selected.includes(item.version_id) && allowed.has(item.lifecycle_status));
  }, [bulkAction, items, selected]);

  const executeBulk = async () => {
    if (!bulkAction || eligibleSelected.length === 0) return;
    setBusyAction("bulk"); setBulkFailures([]);
    try {
      const ids = eligibleSelected.map((item) => item.version_id);
      const result = bulkAction === "publish"
        ? await api.bulkPublishManagedContent(ids)
        : await api.bulkReviewManagedContent(ids, bulkAction === "approve");
      const titles = new Map(eligibleSelected.map((item) => [item.version_id, item.title]));
      const failures = result.results
        .filter((entry) => entry.status === "failed")
        .map((entry) => ({ ...entry, title: titles.get(entry.version_id) || "未知资料" }));
      setBulkFailures(failures);
      if (result.failed) toast.error(`成功 ${result.succeeded} 份，失败 ${result.failed} 份`);
      else toast.success(bulkAction === "publish" ? `已将 ${result.succeeded} 份资料加入发布队列` : `已处理 ${result.succeeded} 份资料`);
      setSelected(failures.map((entry) => entry.version_id)); await load(true);
      if (!result.failed) setBulkAction(null);
    } catch (bulkError) { toast.error(bulkError instanceof Error ? bulkError.message : "批量操作失败"); }
    finally { setBusyAction(null); }
  };

  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const selectable = items.slice(0, BULK_LIMIT);
  const allSelected = selectable.length > 0 && selectable.every((item) => selected.includes(item.version_id));
  const toggleAll = () => setSelected(allSelected ? [] : selectable.map((item) => item.version_id));
  const hasReviewableSelection = items.some((item) => selected.includes(item.version_id) && item.lifecycle_status === "awaiting_review");
  const hasPublishableSelection = items.some((item) => selected.includes(item.version_id) && ["approved", "publication_failed"].includes(item.lifecycle_status));
  const bulkDisabled = Boolean(busyAction) || refreshing || !enabled;

  const renderActions = (item: ManagedContentItem) => {
    const disabled = Boolean(busyAction) || refreshing || !enabled;
    const draftLike = ["draft", "rejected"].includes(item.lifecycle_status);
    const canDelete = (draftLike && can("organize")) || (!draftLike && can("publish"));
    const deleteBlocked = item.lifecycle_status === "publishing";
    return <div className="flex min-h-control-sm flex-wrap gap-2 sm:justify-end"><PublicationFailure item={item} />
      <Button size="sm" variant="outline" disabled={disabled} onClick={() => setDetail(item)}><Eye className="size-4" />查看</Button>
      {can("organize") && ["draft", "rejected"].includes(item.lifecycle_status) && <Button size="sm" variant="outline" disabled={disabled} onClick={() => { setMoveTarget(item); setMoveFolderId(item.category_id); }}><Move className="size-4" />移动</Button>}
      {can("review") && item.lifecycle_status === "awaiting_review" && <Button size="sm" variant="outline" disabled={disabled} onClick={() => { setMoveTarget(item); setMoveFolderId(item.category_id); }}><Move className="size-4" />移动</Button>}
      {can("organize") && ["draft", "rejected"].includes(item.lifecycle_status) && <Button size="sm" variant="outline" disabled={disabled} onClick={() => void act(item, "submit", () => api.submitManagedContent(item.version_id), "已提交确认")}><Send className="size-4" />{busyAction === `${item.version_id}:submit` ? "提交中…" : "提交"}</Button>}
      {can("review") && item.lifecycle_status === "awaiting_review" && <>
        <Button size="sm" disabled={disabled} onClick={() => void act(item, "approve", () => api.reviewManagedContent(item.version_id, true), "资料已确认")}><Check className="size-4" />{busyAction === `${item.version_id}:approve` ? "确认中…" : "确认"}</Button>
        <Button size="sm" variant="outline" disabled={disabled} onClick={() => void act(item, "reject", () => api.reviewManagedContent(item.version_id, false), "资料已退回")}><X className="size-4" />{busyAction === `${item.version_id}:reject` ? "退回中…" : "退回"}</Button>
      </>}
      {can("publish") && ["approved", "publication_failed"].includes(item.lifecycle_status) && <Button size="sm" disabled={disabled} onClick={() => void act(item, "publish", () => api.publishManagedContent(item.version_id), "已进入发布队列")}><Rocket className="size-4" />{busyAction === `${item.version_id}:publish` ? "发布中…" : item.lifecycle_status === "publication_failed" ? "重新发布" : "发布"}</Button>}
      {canDelete && <Button size="sm" variant="destructive" disabled={disabled || deleteBlocked} title={deleteBlocked ? "资料正在发布，暂时不能移入回收站" : undefined} onClick={() => { setDeleteError(null); setDeleteTarget(item); }}><Trash2 className="size-4" />移至回收站</Button>}
    </div>;
  };

  if (view === "trash") {
    const trashPageCount = Math.max(1, Math.ceil(trashTotal / PAGE_SIZE));
    return <section className="space-y-5" aria-labelledby="managed-content-title">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between"><div><p className="text-ui-xs text-muted-foreground">资料管理</p><h1 id="managed-content-title" className="mt-1 text-ui-2xl font-semibold">回收站</h1><p className="mt-1 text-ui-sm text-muted-foreground">查看和恢复已移出资料库的资料。</p></div><Button size="sm" variant="outline" onClick={() => void loadTrash()} disabled={trashLoading}><RefreshCw className={trashLoading ? "size-4 animate-spin" : "size-4"} />刷新</Button></header>
      <div className="flex gap-2" role="tablist" aria-label="资料视图"><Button size="sm" variant="outline" role="tab" aria-selected="false" onClick={() => { setView("library"); setPage(0); }}>资料库</Button><Button size="sm" role="tab" aria-selected="true">回收站</Button></div>
      {error && <ErrorState title="回收站加载失败" description={error} action={<Button size="sm" variant="outline" onClick={() => void loadTrash()}>重新加载</Button>} />}
      <Card className="overflow-hidden shadow-surface"><div className="flex flex-col gap-3 border-b border-border px-4 py-4 sm:flex-row sm:items-end sm:justify-between sm:px-5"><label className="max-w-xl flex-1 space-y-1 text-ui-xs text-muted-foreground"><span>搜索回收站</span><span className="relative block"><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2" /><Input className="pl-9" value={queryInput} onChange={(event) => setQueryInput(event.target.value)} placeholder="搜索名称或文件名…" /></span></label><p className="text-ui-xs text-muted-foreground">共 {trashTotal} 份</p></div>
        {trashLoading ? <LoadingState className="min-h-48 border-0" label="正在加载回收站…" /> : trashItems.length === 0 ? <EmptyState className="rounded-none border-0" title="回收站为空" description="移至回收站的资料会显示在这里。" /> : <ul className="divide-y divide-border">{trashItems.map((item) => <li key={item.item_id} className="flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5"><div className="min-w-0"><p className="break-words font-medium">{item.title}</p><p className="mt-1 break-all text-ui-xs text-muted-foreground">{item.original_filename} · 原状态：{statusLabel[item.pre_archive_lifecycle_status || item.lifecycle_status] || "未知"}</p><p className="mt-1 text-ui-xs text-muted-foreground">{item.archived_by_name || "未知人员"} 于 {item.archived_at ? new Date(item.archived_at * 1000).toLocaleString("zh-CN") : "未知时间"} 移入回收站</p></div><Button size="sm" variant="outline" disabled={Boolean(busyAction)} onClick={() => { setRestoreError(null); setRestoreTarget(item); }}><ArchiveRestore className="size-4" />恢复</Button></li>)}</ul>}
        <div className="flex items-center justify-between border-t border-border px-4 py-3 sm:px-5"><p className="text-ui-xs text-muted-foreground">第 {page + 1} / {trashPageCount} 页</p><div className="flex gap-2"><Button size="sm" variant="outline" disabled={page === 0 || trashLoading} onClick={() => setPage((value) => value - 1)}>上一页</Button><Button size="sm" variant="outline" disabled={page + 1 >= trashPageCount || trashLoading} onClick={() => setPage((value) => value + 1)}>下一页</Button></div></div>
      </Card>
      <Dialog open={Boolean(restoreTarget)} onOpenChange={(open) => { if (!open && !busyAction) { setRestoreTarget(null); setRestoreError(null); } }}><DialogContent><DialogHeader><DialogTitle>恢复资料</DialogTitle><DialogDescription>“{restoreTarget?.title}”将恢复到资料库。已发布或发布失败的资料会恢复为“已确认”，需要管理员重新发布后才会进入检索。</DialogDescription></DialogHeader>{restoreError && <p className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui-sm text-destructive" role="alert">{restoreError}</p>}<DialogFooter><Button variant="outline" disabled={Boolean(busyAction)} onClick={() => setRestoreTarget(null)}>取消</Button><Button disabled={Boolean(busyAction)} onClick={() => void restoreContent()}>{busyAction ? "恢复中…" : "确认恢复"}</Button></DialogFooter></DialogContent></Dialog>
    </section>;
  }

  return <section className="space-y-5" aria-labelledby="managed-content-title">
    <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div><p className="text-ui-xs text-muted-foreground">资料管理</p><h1 id="managed-content-title" className="mt-1 text-ui-2xl font-semibold text-foreground">资料库</h1><p className="mt-1 text-ui-sm text-muted-foreground">统一管理资料的上传、分类、确认和发布。</p></div>
    </header>

    {(can("review") || can("publish")) && <div className="flex gap-2" role="tablist" aria-label="资料视图"><Button size="sm" role="tab" aria-selected="true">资料库</Button><Button size="sm" variant="outline" role="tab" aria-selected="false" onClick={() => { setView("trash"); setPage(0); setSelected([]); }}>回收站</Button></div>}

    <section className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="资料状态概览">
      {[["全部资料", Object.values(counts).reduce((sum, value) => sum + value, 0)], ["待确认", counts.awaiting_review || 0], ["已确认", counts.approved || 0], ["已发布", counts.published || 0]].map(([label, value]) => <Card key={label} className="overflow-hidden shadow-surface"><CardContent className="relative p-4 pt-4"><span className="absolute inset-x-0 top-0 h-1 bg-primary/80" aria-hidden="true" /><p className="text-ui-xs font-medium text-muted-foreground">{label}</p><p className="mt-2 text-ui-xl font-semibold tabular-nums text-foreground">{value}</p></CardContent></Card>)}
    </section>

    {!enabled && !loading && <div className="border border-warning/40 bg-warning/10 px-4 py-3 text-ui-sm" role="status">资料库当前未启用，上传和流程操作暂不可用。</div>}
    {error && <ErrorState title="资料列表加载失败" description={error} action={<Button size="sm" variant="outline" onClick={() => void load()}>重新加载</Button>} />}

    {(can("review") || can("manage_categories")) && folderRequests.length > 0 && <Card className="overflow-hidden shadow-surface" aria-labelledby="folder-requests-title"><div className="border-b border-border px-4 py-3 sm:px-5"><h2 id="folder-requests-title" className="text-ui-base font-semibold">待处理目录申请</h2></div><ul className="divide-y divide-border">{folderRequests.map((request) => <li key={request.id} className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5"><div className="min-w-0"><p className="break-words text-ui-sm font-medium">{request.display_name}</p><p className="mt-0.5 text-ui-xs text-muted-foreground">上级目录：{request.parent_label} · 申请人：{request.requester_name || "未知"}</p></div><div className="flex gap-2"><Button size="sm" variant="outline" disabled={busyAction === `folder-request:${request.id}`} onClick={() => void reviewFolder(request, false)}><X className="size-4" />退回</Button><Button size="sm" disabled={busyAction === `folder-request:${request.id}`} onClick={() => void reviewFolder(request, true)}><Check className="size-4" />批准</Button></div></li>)}</ul></Card>}
    <Card className="overflow-hidden shadow-surface [&_table]:!min-w-[56rem]" aria-labelledby="managed-list-title">
      <div className="flex flex-col gap-3 border-b border-border px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5"><div><h2 id="managed-list-title" className="text-ui-base font-semibold">资料列表</h2><p className="mt-1 text-ui-xs text-muted-foreground">当前目录：{currentFolder?.full_path || "请选择目录"} · 共 {total} 份</p></div><div className="flex flex-wrap gap-2">{can("organize") && <Button size="sm" className="min-h-10" onClick={openUploadDialog} disabled={!enabled || !currentFolderId || uploading}><Upload className="size-4" />上传文件</Button>}{(can("organize") || can("manage_categories")) && <Button size="sm" variant="outline" onClick={() => can("manage_categories") ? setNewFolderOpen(true) : setRequestFolderOpen(true)} disabled={!currentFolder || currentFolder.level >= 4}><FolderPlus className="size-4" />新建</Button>}<Button size="sm" variant="outline" onClick={() => void load(true)} disabled={loading || refreshing}><RefreshCw className={refreshing ? "size-4 animate-spin" : "size-4"} />{refreshing ? "刷新中…" : "刷新"}</Button></div></div>
      <div className="border-b border-border bg-surface-muted/40 px-4 py-3 sm:px-5" data-testid="managed-folder-address"><nav className="flex min-w-0 items-center gap-1 rounded-ui-md border border-input bg-background px-3 py-2 text-ui-sm" aria-label="资料路径"><button type="button" className="shrink-0 rounded px-1 py-0.5 font-medium hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => setCurrentFolderId("")}>/</button>{breadcrumbs.map((folder) => <span key={folder.id} className="flex min-w-0 items-center gap-1"><ChevronRight className="size-4 shrink-0 text-muted-foreground" /><button type="button" className="max-w-56 truncate rounded px-1 py-0.5 hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => setCurrentFolderId(folder.id)}>{folder.display_code} {folder.display_name}</button></span>)}</nav></div>
      <div className="grid gap-2 border-b border-border p-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4" aria-label="子文件夹">
        {childFolders.map((folder) => <button key={folder.id} type="button" className={`flex min-h-14 items-center gap-3 rounded-ui-lg border bg-background px-3 py-2 text-left transition-colors hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${draggedItem ? "border-primary/60" : "border-border"}`} onClick={() => setCurrentFolderId(folder.id)} onDragOver={(event) => { if (draggedItem) event.preventDefault(); }} onDrop={(event) => { event.preventDefault(); if (draggedItem) void moveItemTo(draggedItem, folder.id); }}><Folder className="size-5 shrink-0 text-primary" /><span className="min-w-0"><span className="block truncate text-ui-sm font-medium">{folder.display_code} {folder.display_name}</span><span className="block text-ui-xs text-muted-foreground">{folder.item_count} 份直接资料</span></span></button>)}
        {!loading && childFolders.length === 0 && <p className="col-span-full px-1 py-2 text-ui-sm text-muted-foreground">当前目录没有子文件夹。</p>}
      </div>
      <div className="grid gap-2 border-t border-border px-4 py-4 md:grid-cols-2 xl:grid-cols-[minmax(12rem,1fr)_minmax(10rem,12rem)_9rem_9rem_auto] xl:items-end sm:px-5">
        <label className="space-y-1 text-ui-xs text-muted-foreground md:col-span-2 xl:col-span-1"><span>搜索</span><span className="relative block"><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input className="pl-9" value={queryInput} onChange={(event) => setQueryInput(event.target.value)} placeholder="搜索名称、文件名或分类…" /></span></label>
        <label className="space-y-1 text-ui-xs text-muted-foreground"><span>分类</span><Select value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}><option value="">全部分类</option>{categories.map((category) => <option key={category.id} value={category.id}>{category.full_path || category.display_name}</option>)}</Select></label>
        <label className="space-y-1 text-ui-xs text-muted-foreground"><span>状态</span><Select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">全部状态</option>{Object.entries(statusLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></label>
        <label className="space-y-1 text-ui-xs text-muted-foreground"><span>来源</span><Select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}><option value="">全部来源</option>{Object.entries(sourceLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></label>
        <Button variant="outline" onClick={() => { setQueryInput(""); setCategoryFilter(""); setStatusFilter(""); setSourceFilter(""); }}>清除筛选</Button>
      </div>

      <div className="flex min-h-[6.75rem] flex-col justify-center gap-3 border-t border-border bg-surface-muted px-4 py-3 sm:min-h-14 sm:flex-row sm:items-center sm:justify-between sm:px-5" data-testid="managed-bulk-toolbar"><p className="text-ui-sm" role="status" aria-live="polite">{selected.length > 0 ? <>已选择 <strong>{selected.length}</strong> 份，单次最多 {BULK_LIMIT} 份</> : <>未选择资料，单次最多 {BULK_LIMIT} 份</>}</p><div className="flex flex-wrap gap-2">{can("review") && <><Button size="sm" disabled={bulkDisabled || !hasReviewableSelection} onClick={() => setBulkAction("approve")}><Check className="size-4" />批量确认</Button><Button size="sm" variant="outline" disabled={bulkDisabled || !hasReviewableSelection} onClick={() => setBulkAction("reject")}><X className="size-4" />批量退回</Button></>}{can("publish") && <Button size="sm" disabled={bulkDisabled || !hasPublishableSelection} onClick={() => setBulkAction("publish")}><Rocket className="size-4" />批量发布</Button>}</div></div>

      <div data-testid="managed-content-drop-list" className={`relative transition-colors duration-normal ${listDropActive ? "bg-primary/5 ring-2 ring-inset ring-primary/50" : ""}`} onDragEnter={(event) => { if (event.dataTransfer?.types.includes("Files")) { event.preventDefault(); setListDropActive(true); } }} onDragOver={(event) => { if (event.dataTransfer?.types.includes("Files")) event.preventDefault(); }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setListDropActive(false); }} onDrop={(event) => { if (!event.dataTransfer?.types.includes("Files") || !event.dataTransfer.files.length) return; event.preventDefault(); prepareFolderUpload(Array.from(event.dataTransfer.files)); }}>
      {uploadResults.length > 0 && <ul className="border-t border-border px-4 py-3 text-ui-sm sm:px-5" aria-live="polite">{uploadResults.map((entry) => <li key={entry.filename} className="flex items-start justify-between gap-3 border-b border-border py-2 last:border-b-0"><span className="min-w-0"><span className="block break-all">{entry.filename}</span>{entry.reason && <span className="mt-0.5 block break-words text-ui-xs text-muted-foreground">{entry.reason}</span>}</span><Badge className="shrink-0" variant={entry.status === "accepted" ? "success" : "warning"}>{entry.status === "accepted" ? "已接收" : "已跳过"}</Badge></li>)}</ul>}
      {loading ? <LoadingState className="min-h-48 border-x-0 border-b-0" label="正在加载资料…" /> : !error && items.length === 0 ? <EmptyState className="rounded-none border-x-0 border-b-0" title="没有符合条件的资料" description="请调整筛选条件或上传新资料。" /> : !error && <>
        <div className="hidden overflow-x-auto border-t border-border lg:block"><table className="w-full min-w-[64rem] text-ui-sm"><thead className="border-b border-border bg-surface-muted text-left text-muted-foreground"><tr><th className="w-12 px-3 py-3"><Checkbox aria-label="选择当前页前20份资料" checked={allSelected} onChange={toggleAll} /></th>{([ ["title", "资料"], ["category", "分类"], ["status", "状态"], ["source", "来源"] ] as [SortKey, string][]).map(([key, label]) => <th key={key} aria-sort={sort?.key === key ? sort.direction === "asc" ? "ascending" : "descending" : "none"} className="px-3 py-3 font-medium"><button type="button" className="inline-flex items-center gap-1 rounded px-1 py-0.5 hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => toggleSort(key)}>{label}{sortIcon(key)}</button></th>)}<th className="px-3 py-3 text-right font-medium">操作</th></tr></thead><tbody className="divide-y divide-border">{sortedItems.map((item, index) => { const movable = (can("organize") && ["draft", "rejected"].includes(item.lifecycle_status)) || (can("review") && item.lifecycle_status === "awaiting_review"); return <tr key={item.item_id} draggable={movable} title={movable ? "拖动到上方文件夹可移动资料" : undefined} onDragStart={() => setDraggedItem(item)} onDragEnd={() => setDraggedItem(null)} className={`transition-colors duration-normal hover:bg-surface-muted/60 ${movable ? "cursor-grab" : ""}`}><td className="px-3 py-3"><Checkbox aria-label={`选择${item.title}`} checked={selected.includes(item.version_id)} disabled={index >= BULK_LIMIT} onChange={() => setSelected((current) => current.includes(item.version_id) ? current.filter((id) => id !== item.version_id) : [...current, item.version_id].slice(0, BULK_LIMIT))} /></td><td className="max-w-xs px-3 py-3"><p className="break-words font-medium">{item.title}</p><p className="mt-0.5 break-all text-ui-xs text-muted-foreground">{item.original_filename} · v{item.version_number}</p></td><td className="max-w-xs px-3 py-3 break-words">{item.category_path || item.category_label}</td><td className="px-3 py-3"><Badge variant={statusVariant(item.lifecycle_status)}>{statusLabel[item.lifecycle_status] || "未知状态"}</Badge></td><td className="px-3 py-3">{sourceLabel[item.source_origin] || "其他来源"}</td><td className="px-3 py-3">{renderActions(item)}</td></tr>; })}</tbody></table></div>
        <ul className="divide-y divide-border border-t border-border lg:hidden">{items.map((item, index) => <li key={item.item_id} className="space-y-3 px-4 py-4 sm:px-5"><div className="flex items-start gap-3"><Checkbox className="mt-0.5" aria-label={`选择${item.title}`} checked={selected.includes(item.version_id)} disabled={index >= BULK_LIMIT} onChange={() => setSelected((current) => current.includes(item.version_id) ? current.filter((id) => id !== item.version_id) : [...current, item.version_id].slice(0, BULK_LIMIT))} /><div className="min-w-0 flex-1"><div className="flex items-start justify-between gap-3"><p className="break-words font-medium">{item.title}</p><Badge className="shrink-0" variant={statusVariant(item.lifecycle_status)}>{statusLabel[item.lifecycle_status] || "未知状态"}</Badge></div><p className="mt-1 break-all text-ui-xs text-muted-foreground">{item.original_filename} · v{item.version_number}</p></div></div><dl className="grid grid-cols-[4rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-ui-sm"><dt className="text-muted-foreground">分类</dt><dd className="break-words">{item.category_path || item.category_label}</dd><dt className="text-muted-foreground">来源</dt><dd>{sourceLabel[item.source_origin] || "其他来源"}</dd></dl>{renderActions(item)}</li>)}</ul>
        <div className="flex flex-col gap-2 border-t border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5"><p className="text-ui-xs text-muted-foreground">共 {total} 份，第 {page + 1} / {pageCount} 页</p><div className="flex flex-wrap items-center justify-end gap-2"><label className="flex items-center gap-2 text-ui-xs text-muted-foreground">每页<Select aria-label="每页条数" className="h-control-sm w-20" value={String(pageSize)} onChange={(event) => setPageSize(Number(event.target.value))}>{PAGE_SIZE_OPTIONS.map((size) => <option key={size} value={size}>{size} 条</option>)}</Select></label><Button size="sm" variant="outline" disabled={page === 0 || loading} onClick={() => setPage((value) => value - 1)}>上一页</Button><Select aria-label="跳转页码" className="h-control-sm w-24" value={String(page + 1)} onChange={(event) => setPage(Number(event.target.value) - 1)} disabled={loading}>{Array.from({ length: pageCount }, (_, index) => <option key={index + 1} value={index + 1}>第 {index + 1} 页</option>)}</Select><Button size="sm" variant="outline" disabled={page + 1 >= pageCount || loading} onClick={() => setPage((value) => value + 1)}>下一页</Button></div></div>
      </>}
      </div>
    </Card>

    <Dialog open={Boolean(bulkAction)} onOpenChange={(open) => { if (!open) { setBulkAction(null); setBulkFailures([]); } }}><DialogContent><DialogHeader><DialogTitle>{bulkAction === "publish" ? "批量发布资料" : bulkAction === "reject" ? "批量退回资料" : "批量确认资料"}</DialogTitle><DialogDescription>本次将处理 {eligibleSelected.length} 份符合条件的资料。系统会逐项执行并保留失败原因。</DialogDescription></DialogHeader>{bulkFailures.length > 0 && <div className="space-y-2 text-ui-sm text-destructive" role="alert"><p>上次操作有 {bulkFailures.length} 份失败：</p><ul className="max-h-48 space-y-1 overflow-y-auto border-y border-destructive/30 py-2">{bulkFailures.map((entry) => <li key={entry.version_id} className="break-words"><span className="font-medium">{entry.title}</span>{entry.message ? `：${entry.message}` : "：请刷新后重试"}</li>)}</ul></div>}<DialogFooter><Button variant="outline" onClick={() => setBulkAction(null)} disabled={busyAction === "bulk"}>取消</Button><Button onClick={() => void executeBulk()} disabled={busyAction === "bulk" || eligibleSelected.length === 0}>{busyAction === "bulk" ? "处理中…" : bulkFailures.length ? "重试失败项" : "确认执行"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={Boolean(detail) && !previewState.parentId} onOpenChange={(open) => { if (!open && !previewState.parentId) setDetail(null); }}><DialogContent className="max-w-2xl"><DialogHeader><DialogTitle>{detail?.title || "资料详情"}</DialogTitle><DialogDescription>核对文件、分类、来源和版本后再确认或发布。</DialogDescription></DialogHeader>{detail && <div className="space-y-4"><PublicationFailure item={detail} /><dl className="grid grid-cols-[5rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-ui-sm"><dt className="text-muted-foreground">文件名</dt><dd className="break-all">{detail.original_filename}</dd><dt className="text-muted-foreground">分类</dt><dd className="break-words">{detail.category_path || detail.category_label}</dd><dt className="text-muted-foreground">状态</dt><dd><Badge variant={statusVariant(detail.lifecycle_status)}>{statusLabel[detail.lifecycle_status]}</Badge></dd><dt className="text-muted-foreground">来源</dt><dd>{sourceLabel[detail.source_origin] || "其他来源"}</dd><dt className="text-muted-foreground">版本</dt><dd>v{detail.version_number}</dd><dt className="text-muted-foreground">创建时间</dt><dd>{formatAdminDate(detail.created_at)}</dd><dt className="text-muted-foreground">最后更新时间</dt><dd>{formatAdminDate(detail.updated_at)}</dd><dt className="text-muted-foreground">发布尝试</dt><dd>共 {detail.publication_attempt_count} 次</dd></dl><div className="flex flex-col gap-2 sm:flex-row">{detail.preview_parent_id && ["pdf", "docx", "xlsx", "pptx"].includes(detail.doc_type) ? <Button variant="outline" onClick={() => { openDocumentPreview(detail.preview_parent_id!, detail.title, detail.doc_type, 1, {}, "managed-content-detail"); }}><Eye className="size-4" />预览文件</Button> : <a className={buttonVariants({ variant: "outline" })} href={api.managedContentFileUrl(detail.version_id)} target="_blank" rel="noreferrer"><Eye className="size-4" />打开文件</a>}<a className={buttonVariants({ variant: "outline" })} href={api.managedContentFileUrl(detail.version_id, true)}><Download className="size-4" />下载</a></div></div>}</DialogContent></Dialog>

    <Dialog open={requestFolderOpen} onOpenChange={setRequestFolderOpen}><DialogContent><DialogHeader><DialogTitle>申请新建文件夹</DialogTitle><DialogDescription>申请将在“{currentFolder?.display_name || "当前目录"}”下创建受控目录，由资料负责人审批。</DialogDescription></DialogHeader><label className="space-y-1.5 text-ui-sm font-medium"><span>文件夹名称</span><Input value={requestFolderName} onChange={(event) => setRequestFolderName(event.target.value)} placeholder="例如：净高分析" autoFocus /></label><DialogFooter><Button variant="outline" onClick={() => setRequestFolderOpen(false)} disabled={busyAction === "request-folder"}>取消</Button><Button onClick={() => void requestFolder()} disabled={!requestFolderName.trim() || busyAction === "request-folder"}>{busyAction === "request-folder" ? "提交中…" : "提交申请"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={newFolderOpen} onOpenChange={setNewFolderOpen}><DialogContent><DialogHeader><DialogTitle>新建文件夹</DialogTitle><DialogDescription>文件夹将建立在“{currentFolder?.display_name || "当前目录"}”下，最多支持四级目录。</DialogDescription></DialogHeader><label className="space-y-1.5 text-ui-sm font-medium"><span>文件夹名称</span><Input value={newFolderName} onChange={(event) => setNewFolderName(event.target.value)} placeholder="例如：净高分析" autoFocus /></label><DialogFooter><Button variant="outline" onClick={() => setNewFolderOpen(false)} disabled={busyAction === "new-folder"}>取消</Button><Button onClick={() => void createFolder()} disabled={!newFolderName.trim() || busyAction === "new-folder"}>{busyAction === "new-folder" ? "创建中…" : "创建"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={Boolean(moveTarget)} onOpenChange={(open) => { if (!open) setMoveTarget(null); }}><DialogContent><DialogHeader><DialogTitle>移动资料</DialogTitle><DialogDescription>将“{moveTarget?.title || "资料"}”移动到另一个受控目录。已确认或已发布资料需要先退回。</DialogDescription></DialogHeader><label className="space-y-1.5 text-ui-sm font-medium"><span>目标目录</span><Select value={moveFolderId} onChange={(event) => setMoveFolderId(event.target.value)}>{categories.filter((category) => category.is_active).map((category) => <option key={category.id} value={category.id}>{category.full_path || `${category.display_code} ${category.display_name}`}</option>)}</Select></label><DialogFooter><Button variant="outline" onClick={() => setMoveTarget(null)} disabled={Boolean(busyAction?.endsWith(":move"))}>取消</Button><Button onClick={() => void moveContent()} disabled={!moveFolderId || moveFolderId === moveTarget?.category_id || Boolean(busyAction?.endsWith(":move"))}>{busyAction?.endsWith(":move") ? "移动中…" : "移动"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={uploadDialogOpen} onOpenChange={(open) => { if (!open) closeUploadDialog(); }}><DialogContent><DialogHeader><DialogTitle>上传文件</DialogTitle><DialogDescription>文件将上传到当前目录“{currentFolder?.full_path || "请选择目录"}”，上传后先进入待提交状态。</DialogDescription></DialogHeader><label onDragEnter={(event) => { event.preventDefault(); setDragActive(true); }} onDragOver={(event) => event.preventDefault()} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragActive(false); }} onDrop={(event) => { event.preventDefault(); setDragActive(false); acceptFiles(Array.from(event.dataTransfer?.files || [])); }} className={`flex min-h-36 cursor-pointer flex-col items-center justify-center gap-2 rounded-ui-lg border border-dashed px-4 py-6 text-center transition-colors duration-normal focus-within:ring-2 focus-within:ring-ring ${dragActive ? "border-primary bg-primary/5" : "border-input bg-background hover:bg-surface-muted"}`}><Upload className="size-6 text-primary" /><span className="text-ui-sm font-medium">拖动文件到这里，或选择文件</span><span className="text-ui-xs text-muted-foreground">支持 PDF、Markdown、Word、Excel 和 PPT</span><input ref={fileInputRef} aria-label="选择资料文件" type="file" multiple accept=".pdf,.md,.docx,.xlsx,.pptx" className="sr-only" disabled={uploading} onChange={(event) => acceptFiles(Array.from(event.target.files || []))} /></label>{files.length > 0 && <ul className="max-h-40 space-y-1 overflow-y-auto rounded-ui-md border border-border px-3 py-2 text-ui-sm">{files.map((file) => <li key={`${file.name}-${file.size}`} className="break-all">{file.name}<span className="ml-2 text-ui-xs text-muted-foreground">{Math.ceil(file.size / 1024)} KB</span></li>)}</ul>}<DialogFooter><Button variant="outline" onClick={closeUploadDialog} disabled={uploading}>取消</Button><Button onClick={() => void confirmDialogUpload()} disabled={!files.length || uploading || !currentFolderId}>{uploading ? "上传中…" : "确定上传"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={pendingUploadFiles.length > 0} onOpenChange={(open) => { if (!open && !uploading) { setPendingUploadFiles([]); setPendingUploadFolderId(""); } }}><DialogContent><DialogHeader><DialogTitle>确认上传</DialogTitle><DialogDescription>将上传到“{categories.find((category) => category.id === pendingUploadFolderId)?.full_path || currentFolder?.full_path || "当前目录"}”，确认后文件会进入待提交状态。</DialogDescription></DialogHeader><div className="space-y-2 text-ui-sm"><p>共 {pendingUploadFiles.length} 个文件</p><ul className="max-h-48 space-y-1 overflow-y-auto rounded-ui-md border border-border px-3 py-2">{pendingUploadFiles.map((file) => <li key={`${file.name}-${file.size}`} className="break-all">{file.name}<span className="ml-2 text-ui-xs text-muted-foreground">{Math.ceil(file.size / 1024)} KB</span></li>)}</ul></div><DialogFooter><Button variant="outline" onClick={() => { setPendingUploadFiles([]); setPendingUploadFolderId(""); }} disabled={uploading}>取消</Button><Button onClick={() => void confirmFolderUpload()} disabled={uploading}>{uploading ? "上传中…" : "确定上传"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={Boolean(deleteTarget)} onOpenChange={(open) => { if (!open && busyAction !== `${deleteTarget?.version_id}:delete`) { setDeleteTarget(null); setDeleteError(null); } }}><DialogContent><DialogHeader><DialogTitle>移至回收站</DialogTitle><DialogDescription>“{deleteTarget?.title}”将从资料列表和知识库检索中移除，但文件、版本及审核发布历史会保留，可由资料负责人或系统管理员恢复。</DialogDescription></DialogHeader>{deleteError && <p className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui-sm text-destructive" role="alert">{deleteError}</p>}<DialogFooter><Button variant="outline" disabled={busyAction === `${deleteTarget?.version_id}:delete`} onClick={() => { setDeleteTarget(null); setDeleteError(null); }}>取消</Button><Button variant="destructive" disabled={busyAction === `${deleteTarget?.version_id}:delete`} onClick={() => void deleteContent()}>{busyAction === `${deleteTarget?.version_id}:delete` ? "处理中…" : "确认移入"}</Button></DialogFooter></DialogContent></Dialog>
  </section>;
}
