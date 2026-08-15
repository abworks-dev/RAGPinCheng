import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, Download, Eye, FileText, RefreshCw, Rocket, Search, Send, Trash2, Upload, X } from "lucide-react";
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
import type { BulkManagedContentResult, ContentPermission, ManagedCategory, ManagedContentItem, ManagedUploadResponse } from "../../types";

const PAGE_SIZE = 25;
const BULK_LIMIT = 20;

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
  const permissions = state.status === "authed" ? state.user.content_permissions || [] : [];
  const can = (permission: ContentPermission) => state.status === "authed" && (state.user.role === "admin" || permissions.includes(permission));
  const [items, setItems] = useState<ManagedContentItem[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [categories, setCategories] = useState<ManagedCategory[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [uploadCategoryId, setUploadCategoryId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
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
  const [detail, setDetail] = useState<ManagedContentItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ManagedContentItem | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(queryInput.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [queryInput]);
  useEffect(() => { setPage(0); setSelected([]); }, [query, categoryFilter, statusFilter, sourceFilter]);

  const load = useCallback(async (refresh = false) => {
    refresh ? setRefreshing(true) : setLoading(true);
    setError(null);
    try {
      const [capabilities, categoryRows, listing] = await Promise.all([
        api.managedContentCapabilities(), api.managedCategories(), api.managedContentItems({
          query: query || undefined,
          category_id: categoryFilter || undefined,
          lifecycle_status: statusFilter || undefined,
          source_origin: sourceFilter || undefined,
          limit: PAGE_SIZE,
          offset: page * PAGE_SIZE,
        }),
      ]);
      setEnabled(capabilities.enabled);
      setCategories(categoryRows);
      setItems(listing.items);
      setTotal(listing.total);
      setCounts(listing.status_counts);
      setUploadCategoryId((current) => current || categoryRows[0]?.id || "");
      setSelected((current) => current.filter((id) => listing.items.some((item) => item.version_id === id)));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "资料加载失败");
    } finally {
      setLoading(false); setRefreshing(false);
    }
  }, [categoryFilter, page, query, sourceFilter, statusFilter]);

  useEffect(() => { void load(); }, [load]);

  const upload = async () => {
    setUploading(true); setUploadResults([]);
    try {
      const result = await api.uploadManagedContent(files, uploadCategoryId);
      setUploadResults(result.entries);
      const accepted = result.entries.filter((entry) => entry.status === "accepted").length;
      const skipped = result.entries.length - accepted;
      toast.success(skipped ? `已接收 ${accepted} 个文件，跳过 ${skipped} 个` : `已接收 ${accepted} 个文件`);
      setFiles([]); if (fileInputRef.current) fileInputRef.current.value = "";
      await load(true);
    } catch (uploadError) { toast.error(uploadError instanceof Error ? uploadError.message : "上传失败"); }
    finally { setUploading(false); }
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
      toast.success(`已删除“${target.title}”`);
      await load(true);
    } catch (deleteFailure) {
      setDeleteError(deleteFailure instanceof Error ? deleteFailure.message : "删除资料失败");
    } finally {
      setBusyAction(null);
    }
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

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
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
      {can("organize") && ["draft", "rejected"].includes(item.lifecycle_status) && <Button size="sm" variant="outline" disabled={disabled} onClick={() => void act(item, "submit", () => api.submitManagedContent(item.version_id), "已提交确认")}><Send className="size-4" />{busyAction === `${item.version_id}:submit` ? "提交中…" : "提交"}</Button>}
      {can("review") && item.lifecycle_status === "awaiting_review" && <>
        <Button size="sm" disabled={disabled} onClick={() => void act(item, "approve", () => api.reviewManagedContent(item.version_id, true), "资料已确认")}><Check className="size-4" />{busyAction === `${item.version_id}:approve` ? "确认中…" : "确认"}</Button>
        <Button size="sm" variant="outline" disabled={disabled} onClick={() => void act(item, "reject", () => api.reviewManagedContent(item.version_id, false), "资料已退回")}><X className="size-4" />{busyAction === `${item.version_id}:reject` ? "退回中…" : "退回"}</Button>
      </>}
      {can("publish") && ["approved", "publication_failed"].includes(item.lifecycle_status) && <Button size="sm" disabled={disabled} onClick={() => void act(item, "publish", () => api.publishManagedContent(item.version_id), "已进入发布队列")}><Rocket className="size-4" />{busyAction === `${item.version_id}:publish` ? "发布中…" : item.lifecycle_status === "publication_failed" ? "重新发布" : "发布"}</Button>}
      {canDelete && <Button size="sm" variant="destructive" disabled={disabled || deleteBlocked} title={deleteBlocked ? "资料正在发布，暂时不能删除" : undefined} onClick={() => { setDeleteError(null); setDeleteTarget(item); }}><Trash2 className="size-4" />删除</Button>}
    </div>;
  };

  return <section className="space-y-5" aria-labelledby="managed-content-title">
    <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div><p className="text-ui-xs text-muted-foreground">资料管理</p><h1 id="managed-content-title" className="mt-1 text-ui-2xl font-semibold text-foreground">资料库</h1><p className="mt-1 text-ui-sm text-muted-foreground">统一管理资料的上传、分类、确认和发布。</p></div>
      <Button size="sm" variant="outline" className="w-full sm:w-auto" onClick={() => void load(true)} disabled={loading || refreshing}><RefreshCw className={refreshing ? "size-4 animate-spin" : "size-4"} />{refreshing ? "刷新中…" : "刷新"}</Button>
    </header>

    <section className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="资料状态概览">
      {[["全部资料", Object.values(counts).reduce((sum, value) => sum + value, 0)], ["待确认", counts.awaiting_review || 0], ["已确认", counts.approved || 0], ["已发布", counts.published || 0]].map(([label, value]) => <Card key={label} className="overflow-hidden shadow-surface"><CardContent className="relative p-4 pt-4"><span className="absolute inset-x-0 top-0 h-1 bg-primary/80" aria-hidden="true" /><p className="text-ui-xs font-medium text-muted-foreground">{label}</p><p className="mt-2 text-ui-xl font-semibold tabular-nums text-foreground">{value}</p></CardContent></Card>)}
    </section>

    {!enabled && !loading && <div className="border border-warning/40 bg-warning/10 px-4 py-3 text-ui-sm" role="status">资料库当前未启用，上传和流程操作暂不可用。</div>}
    {error && <ErrorState title="资料列表加载失败" description={error} action={<Button size="sm" variant="outline" onClick={() => void load()}>重新加载</Button>} />}

    {can("organize") && <Card className="shadow-surface" aria-labelledby="managed-upload-title">
      <CardHeader className="p-4 pb-0 sm:p-5 sm:pb-0"><CardTitle id="managed-upload-title" className="text-ui-base">上传资料</CardTitle><CardDescription className="text-ui-xs">上传后先进入待提交状态，不会直接进入检索。</CardDescription></CardHeader>
      <CardContent className="space-y-4 p-4 pt-4 sm:p-5 sm:pt-4">
        <div className="grid gap-4 lg:grid-cols-[minmax(14rem,18rem)_minmax(0,1fr)_auto] lg:items-end">
          <label className="space-y-1.5 text-ui-sm font-medium"><span>资料分类</span><Select value={uploadCategoryId} onChange={(event) => setUploadCategoryId(event.target.value)} disabled={loading || uploading || categories.length === 0}>{categories.map((category) => <option key={category.id} value={category.id}>{category.full_path || `${category.display_code} ${category.display_name}`}</option>)}</Select></label>
          <div className="space-y-1.5"><span className="block text-ui-sm font-medium">资料文件</span><label className="flex min-h-20 cursor-pointer items-center gap-3 rounded-ui-lg border border-dashed border-input bg-background px-4 py-3 transition-colors duration-normal hover:bg-surface-muted focus-within:ring-2 focus-within:ring-ring"><FileText className="size-5 shrink-0 text-muted-foreground" /><span className="min-w-0 flex-1"><span className="block text-ui-sm font-medium">{files.length ? `已选择 ${files.length} 个文件` : "选择一个或多个文件"}</span><span className="mt-0.5 block text-ui-xs text-muted-foreground">PDF、Markdown、Word、Excel 或 PPT</span></span><span className="shrink-0 text-ui-sm font-medium text-primary">浏览</span><input ref={fileInputRef} aria-label="选择资料文件" type="file" multiple accept=".pdf,.md,.docx,.xlsx,.pptx" className="sr-only" disabled={uploading} onChange={(event) => { setFiles(Array.from(event.target.files || [])); setUploadResults([]); }} /></label></div>
          <Button className="w-full lg:w-auto" onClick={() => void upload()} disabled={!enabled || uploading || !uploadCategoryId || files.length === 0}><Upload className="size-4" />{uploading ? "上传中…" : "上传资料"}</Button>
        </div>
        {uploadResults.length > 0 && <ul className="divide-y divide-border overflow-hidden rounded-ui-lg border border-border text-ui-sm" aria-live="polite">{uploadResults.map((entry) => <li key={entry.filename} className="flex justify-between gap-3 px-3 py-2"><span className="break-all">{entry.filename}</span><Badge variant={entry.status === "accepted" ? "success" : "warning"}>{entry.status === "accepted" ? "已接收" : "已跳过"}</Badge></li>)}</ul>}
      </CardContent>
    </Card>}

    <Card className="overflow-hidden shadow-surface [&_table]:!min-w-[56rem]" aria-labelledby="managed-list-title">
      <div className="flex flex-col gap-1 px-4 py-4 sm:flex-row sm:items-end sm:justify-between sm:px-5"><div><h2 id="managed-list-title" className="text-ui-base font-semibold">资料列表</h2><p className="mt-1 text-ui-xs text-muted-foreground">分类以数据库中的编号和名称为准。</p></div><p className="text-ui-xs tabular-nums text-muted-foreground">当前共 {total} 份</p></div>
      <div className="grid gap-2 border-t border-border px-4 py-4 md:grid-cols-2 xl:grid-cols-[minmax(12rem,1fr)_minmax(10rem,12rem)_9rem_9rem_auto] xl:items-end sm:px-5">
        <label className="space-y-1 text-ui-xs text-muted-foreground md:col-span-2 xl:col-span-1"><span>搜索</span><span className="relative block"><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input className="pl-9" value={queryInput} onChange={(event) => setQueryInput(event.target.value)} placeholder="搜索名称、文件名或分类…" /></span></label>
        <label className="space-y-1 text-ui-xs text-muted-foreground"><span>分类</span><Select value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}><option value="">全部分类</option>{categories.map((category) => <option key={category.id} value={category.id}>{category.full_path || category.display_name}</option>)}</Select></label>
        <label className="space-y-1 text-ui-xs text-muted-foreground"><span>状态</span><Select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">全部状态</option>{Object.entries(statusLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></label>
        <label className="space-y-1 text-ui-xs text-muted-foreground"><span>来源</span><Select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}><option value="">全部来源</option>{Object.entries(sourceLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></label>
        <Button variant="outline" onClick={() => { setQueryInput(""); setCategoryFilter(""); setStatusFilter(""); setSourceFilter(""); }}>清除筛选</Button>
      </div>

      <div className="flex min-h-[6.75rem] flex-col justify-center gap-3 border-t border-border bg-surface-muted px-4 py-3 sm:min-h-14 sm:flex-row sm:items-center sm:justify-between sm:px-5" data-testid="managed-bulk-toolbar"><p className="text-ui-sm" role="status" aria-live="polite">{selected.length > 0 ? <>已选择 <strong>{selected.length}</strong> 份，单次最多 {BULK_LIMIT} 份</> : <>未选择资料，单次最多 {BULK_LIMIT} 份</>}</p><div className="flex flex-wrap gap-2">{can("review") && <><Button size="sm" disabled={bulkDisabled || !hasReviewableSelection} onClick={() => setBulkAction("approve")}><Check className="size-4" />批量确认</Button><Button size="sm" variant="outline" disabled={bulkDisabled || !hasReviewableSelection} onClick={() => setBulkAction("reject")}><X className="size-4" />批量退回</Button></>}{can("publish") && <Button size="sm" disabled={bulkDisabled || !hasPublishableSelection} onClick={() => setBulkAction("publish")}><Rocket className="size-4" />批量发布</Button>}</div></div>

      {loading ? <LoadingState className="min-h-48 border-x-0 border-b-0" label="正在加载资料…" /> : !error && items.length === 0 ? <EmptyState className="rounded-none border-x-0 border-b-0" title="没有符合条件的资料" description="请调整筛选条件或上传新资料。" /> : !error && <>
        <div className="hidden overflow-x-auto border-t border-border lg:block"><table className="w-full min-w-[64rem] text-ui-sm"><thead className="border-b border-border bg-surface-muted text-left text-muted-foreground"><tr><th className="w-12 px-3 py-3"><Checkbox aria-label="选择当前页前20份资料" checked={allSelected} onChange={toggleAll} /></th><th className="px-3 py-3 font-medium">资料</th><th className="px-3 py-3 font-medium">分类</th><th className="px-3 py-3 font-medium">状态</th><th className="px-3 py-3 font-medium">来源</th><th className="px-3 py-3 text-right font-medium">操作</th></tr></thead><tbody className="divide-y divide-border">{items.map((item, index) => <tr key={item.item_id} className="transition-colors duration-normal hover:bg-surface-muted/60"><td className="px-3 py-3"><Checkbox aria-label={`选择${item.title}`} checked={selected.includes(item.version_id)} disabled={index >= BULK_LIMIT} onChange={() => setSelected((current) => current.includes(item.version_id) ? current.filter((id) => id !== item.version_id) : [...current, item.version_id].slice(0, BULK_LIMIT))} /></td><td className="max-w-xs px-3 py-3"><p className="break-words font-medium">{item.title}</p><p className="mt-0.5 break-all text-ui-xs text-muted-foreground">{item.original_filename} · v{item.version_number}</p></td><td className="max-w-xs px-3 py-3 break-words">{item.category_path || item.category_label}</td><td className="px-3 py-3"><Badge variant={statusVariant(item.lifecycle_status)}>{statusLabel[item.lifecycle_status] || "未知状态"}</Badge></td><td className="px-3 py-3">{sourceLabel[item.source_origin] || "其他来源"}</td><td className="px-3 py-3">{renderActions(item)}</td></tr>)}</tbody></table></div>
        <ul className="divide-y divide-border border-t border-border lg:hidden">{items.map((item, index) => <li key={item.item_id} className="space-y-3 px-4 py-4 sm:px-5"><div className="flex items-start gap-3"><Checkbox className="mt-0.5" aria-label={`选择${item.title}`} checked={selected.includes(item.version_id)} disabled={index >= BULK_LIMIT} onChange={() => setSelected((current) => current.includes(item.version_id) ? current.filter((id) => id !== item.version_id) : [...current, item.version_id].slice(0, BULK_LIMIT))} /><div className="min-w-0 flex-1"><div className="flex items-start justify-between gap-3"><p className="break-words font-medium">{item.title}</p><Badge className="shrink-0" variant={statusVariant(item.lifecycle_status)}>{statusLabel[item.lifecycle_status] || "未知状态"}</Badge></div><p className="mt-1 break-all text-ui-xs text-muted-foreground">{item.original_filename} · v{item.version_number}</p></div></div><dl className="grid grid-cols-[4rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-ui-sm"><dt className="text-muted-foreground">分类</dt><dd className="break-words">{item.category_path || item.category_label}</dd><dt className="text-muted-foreground">来源</dt><dd>{sourceLabel[item.source_origin] || "其他来源"}</dd></dl>{renderActions(item)}</li>)}</ul>
        <div className="flex flex-col gap-2 border-t border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5"><p className="text-ui-xs text-muted-foreground">共 {total} 份，第 {page + 1} / {pageCount} 页</p><div className="flex gap-2"><Button size="sm" variant="outline" disabled={page === 0 || loading} onClick={() => setPage((value) => value - 1)}>上一页</Button><Button size="sm" variant="outline" disabled={page + 1 >= pageCount || loading} onClick={() => setPage((value) => value + 1)}>下一页</Button></div></div>
      </>}
    </Card>

    <Dialog open={Boolean(bulkAction)} onOpenChange={(open) => { if (!open) { setBulkAction(null); setBulkFailures([]); } }}><DialogContent><DialogHeader><DialogTitle>{bulkAction === "publish" ? "批量发布资料" : bulkAction === "reject" ? "批量退回资料" : "批量确认资料"}</DialogTitle><DialogDescription>本次将处理 {eligibleSelected.length} 份符合条件的资料。系统会逐项执行并保留失败原因。</DialogDescription></DialogHeader>{bulkFailures.length > 0 && <div className="space-y-2 text-ui-sm text-destructive" role="alert"><p>上次操作有 {bulkFailures.length} 份失败：</p><ul className="max-h-48 space-y-1 overflow-y-auto border-y border-destructive/30 py-2">{bulkFailures.map((entry) => <li key={entry.version_id} className="break-words"><span className="font-medium">{entry.title}</span>{entry.message ? `：${entry.message}` : "：请刷新后重试"}</li>)}</ul></div>}<DialogFooter><Button variant="outline" onClick={() => setBulkAction(null)} disabled={busyAction === "bulk"}>取消</Button><Button onClick={() => void executeBulk()} disabled={busyAction === "bulk" || eligibleSelected.length === 0}>{busyAction === "bulk" ? "处理中…" : bulkFailures.length ? "重试失败项" : "确认执行"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={Boolean(detail)} onOpenChange={(open) => { if (!open) setDetail(null); }}><DialogContent className="max-w-2xl"><DialogHeader><DialogTitle>{detail?.title || "资料详情"}</DialogTitle><DialogDescription>核对文件、分类、来源和版本后再确认或发布。</DialogDescription></DialogHeader>{detail && <div className="space-y-4"><PublicationFailure item={detail} /><dl className="grid grid-cols-[5rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-ui-sm"><dt className="text-muted-foreground">文件名</dt><dd className="break-all">{detail.original_filename}</dd><dt className="text-muted-foreground">分类</dt><dd className="break-words">{detail.category_path || detail.category_label}</dd><dt className="text-muted-foreground">状态</dt><dd><Badge variant={statusVariant(detail.lifecycle_status)}>{statusLabel[detail.lifecycle_status]}</Badge></dd><dt className="text-muted-foreground">来源</dt><dd>{sourceLabel[detail.source_origin] || "其他来源"}</dd><dt className="text-muted-foreground">版本</dt><dd>v{detail.version_number}</dd><dt className="text-muted-foreground">发布尝试</dt><dd>共 {detail.publication_attempt_count} 次</dd></dl><div className="flex flex-col gap-2 sm:flex-row"><a className={buttonVariants({ variant: "outline" })} href={api.managedContentFileUrl(detail.version_id)} target="_blank" rel="noreferrer"><Eye className="size-4" />{detail.doc_type === "pdf" ? "预览文件" : "打开文件"}</a><a className={buttonVariants({ variant: "outline" })} href={api.managedContentFileUrl(detail.version_id, true)}><Download className="size-4" />下载</a></div></div>}</DialogContent></Dialog>

    <Dialog open={Boolean(deleteTarget)} onOpenChange={(open) => { if (!open && busyAction !== `${deleteTarget?.version_id}:delete`) { setDeleteTarget(null); setDeleteError(null); } }}><DialogContent><DialogHeader><DialogTitle>删除资料</DialogTitle><DialogDescription>“{deleteTarget?.title}”将从资料列表和知识库检索中移除。系统会保留文件、版本及审核发布历史，以便管理员恢复。</DialogDescription></DialogHeader>{deleteError && <p className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui-sm text-destructive" role="alert">{deleteError}</p>}<DialogFooter><Button variant="outline" disabled={busyAction === `${deleteTarget?.version_id}:delete`} onClick={() => { setDeleteTarget(null); setDeleteError(null); }}>取消</Button><Button variant="destructive" disabled={busyAction === `${deleteTarget?.version_id}:delete`} onClick={() => void deleteContent()}>{busyAction === `${deleteTarget?.version_id}:delete` ? "删除中…" : "确认删除"}</Button></DialogFooter></DialogContent></Dialog>
  </section>;
}
