import { Ban, CheckCircle2, CircleAlert, Clock3, ChevronDown, Download, Eye, ListChecks, RefreshCw, Rocket, Search, SlidersHorizontal } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { adminContentApi } from "../../api/admin/content";
import { Badge } from "../../components/ui/badge";
import { Button, buttonVariants } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { Input } from "../../components/ui/input";
import { LoadingState } from "../../components/ui/loading-state";
import { Select } from "../../components/ui/select";
import { toast } from "../../components/ui/toast";
import { useAuth } from "../../context/AuthContext";
import { cn } from "../../lib/utils";
import type { ManagedCategory, ManagedIndexJob, ManagedIndexJobList, UnifiedPublicationJob, UnifiedPublicationJobList } from "../../types";
import { formatAdminDate, formatBytes } from "../../lib/admin-formatters";
import { ManagedSummaryCard } from "../../components/admin/ManagedSummaryCard";
import { ManagedItemType } from "../../components/admin/ManagedItemType";
import { useManagedContentLiveRefresh } from "../../hooks/useManagedContentLiveRefresh";

const PAGE_SIZE = 25;
const PAGE_SIZE_OPTIONS = [25, 50, 100];
const ACTIVE_STATUSES = new Set([
  "pending",
  "uploading",
  "queued_mineru",
  "parsing",
  "chunking",
  "summarizing",
  "embedding",
]);

type StatusFilter = "all" | "processing" | "ready" | "failed";
type StatusVariant = "secondary" | "success" | "warning" | "destructive" | "info";

const EMPTY_LIST: ManagedIndexJobList = { jobs: [], total: 0, status_counts: {} };

const STATUS_META: Record<string, { label: string; hint: string; variant: StatusVariant }> = {
  pending: { label: "排队中", hint: "等待发布处理", variant: "secondary" },
  uploading: { label: "上传中", hint: "正在准备资料", variant: "info" },
  queued_mineru: { label: "等待解析", hint: "等待解析器资源", variant: "warning" },
  parsing: { label: "解析中", hint: "正在提取文档内容", variant: "info" },
  chunking: { label: "切分中", hint: "正在生成检索内容块", variant: "info" },
  summarizing: { label: "生成摘要", hint: "正在处理表格摘要", variant: "warning" },
  embedding: { label: "写入索引", hint: "正在写入向量索引", variant: "warning" },
  done: { label: "已发布", hint: "当前版本可检索", variant: "success" },
  failed: { label: "发布失败", hint: "请查看失败原因", variant: "destructive" },
};

function sourceOriginLabel(sourceOrigin: string | null): string {
  return {
    web: "网页上传",
    server: "服务器导入",
    legacy: "历史迁移",
    transcription: "视频转录",
  }[sourceOrigin || ""] || "其他来源";
}
function IndexTaskSearchFilters({
  searchInput, categoryId, docType, sourceOrigin, status, history, includeArchived, categories,
  onSearchInputChange, onCategoryChange, onDocTypeChange, onSourceChange, onStatusChange,
  onHistoryChange, onIncludeArchivedChange, onClear,
}: {
  searchInput: string; categoryId: string; docType: string; sourceOrigin: string; status: StatusFilter;
  history: boolean; includeArchived: boolean; categories: ManagedCategory[];
  onSearchInputChange: (value: string) => void; onCategoryChange: (value: string) => void;
  onDocTypeChange: (value: string) => void; onSourceChange: (value: string) => void;
  onStatusChange: (value: StatusFilter) => void; onHistoryChange: (value: boolean) => void;
  onIncludeArchivedChange: (value: boolean) => void; onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const filtersId = "publication-task-search-filters";
  const activeFilterCount = Number(Boolean(categoryId)) + Number(Boolean(docType)) + Number(Boolean(sourceOrigin)) + Number(status !== "all") + Number(history) + Number(includeArchived);

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      searchRef.current?.focus();
    };
    document.addEventListener("mousedown", closeOutside);
    document.addEventListener("keydown", closeEscape);
    return () => {
      document.removeEventListener("mousedown", closeOutside);
      document.removeEventListener("keydown", closeEscape);
    };
  }, [open]);

  return <div ref={containerRef} className="relative min-w-0 w-full xl:w-72 xl:max-w-72 xl:justify-self-center min-[1400px]:w-96 min-[1400px]:max-w-96">
    <div className="relative">
      <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
      <Input ref={searchRef} aria-label="搜索发布任务" type="search" value={searchInput} onChange={(event) => onSearchInputChange(event.target.value)} placeholder="搜索名称、文件名或分类…" className="h-control-md pl-9 pr-11 text-ui-xs" />
      <button type="button" className="absolute right-1 top-1/2 flex size-7 -translate-y-1/2 items-center justify-center rounded-ui-sm text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label={open ? "收起发布任务筛选" : "展开发布任务筛选"} title="筛选" aria-haspopup="dialog" aria-expanded={open} aria-controls={filtersId} onClick={() => setOpen((current) => !current)}>
        <SlidersHorizontal className="size-4" aria-hidden="true" />
        {activeFilterCount > 0 && <span className="absolute right-0.5 top-0.5 size-1.5 rounded-full bg-primary" aria-hidden="true" />}
        {activeFilterCount > 0 && <span className="sr-only">，已启用 {activeFilterCount} 项筛选</span>}
      </button>
    </div>
    {open && <div id={filtersId} role="dialog" aria-label="发布任务搜索筛选" className="fixed inset-x-4 bottom-4 top-4 z-dropdown overflow-y-auto rounded-ui-lg border border-border bg-popover p-3 text-popover-foreground shadow-overlay sm:absolute sm:inset-x-auto sm:bottom-auto sm:left-auto sm:right-0 sm:top-full sm:mt-2 sm:min-w-[36rem] sm:overflow-visible">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-ui-xs text-muted-foreground"><span>分类</span><Select className="h-control-sm" aria-label="按数据库分类筛选" value={categoryId} onChange={(event) => onCategoryChange(event.target.value)}><option value="">全部分类</option>{categories.map((category) => <option key={category.id} value={category.id}>{category.full_path}</option>)}</Select></label>
        <label className="space-y-1 text-ui-xs text-muted-foreground"><span>类型</span><Select className="h-control-sm" aria-label="按文件类型筛选" value={docType} onChange={(event) => onDocTypeChange(event.target.value)}><option value="">全部类型</option><option value="pdf">PDF</option><option value="markdown">Markdown</option><option value="docx">Word</option><option value="xlsx">Excel</option><option value="pptx">PPT</option><option value="transcript">视频转写</option></Select></label>
        <label className="space-y-1 text-ui-xs text-muted-foreground"><span>来源</span><Select className="h-control-sm" aria-label="按资料来源筛选" value={sourceOrigin} onChange={(event) => onSourceChange(event.target.value)}><option value="">全部来源</option><option value="web">网页上传</option><option value="server">服务器导入</option><option value="legacy">历史迁移</option><option value="transcription">视频转录</option></Select></label>
        <label className="space-y-1 text-ui-xs text-muted-foreground"><span>发布状态</span><Select className="h-control-sm" aria-label="按发布状态筛选" value={status} onChange={(event) => onStatusChange(event.target.value as StatusFilter)}><option value="all">全部状态</option><option value="processing">处理中</option><option value="ready">已发布</option><option value="failed">发布失败</option></Select></label>
      </div>
      <div className="mt-3 flex flex-col gap-2 border-t border-border pt-3 text-ui-sm sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap gap-4"><label className="flex min-h-control-md cursor-pointer items-center gap-2"><Checkbox checked={includeArchived} onChange={(event) => onIncludeArchivedChange(event.target.checked)} /><span>包含回收站资料</span></label><label className="flex min-h-control-md cursor-pointer items-center gap-2"><Checkbox checked={history} onChange={(event) => onHistoryChange(event.target.checked)} /><span>查看历史尝试</span></label></div>
        <Button size="sm" variant="outline" onClick={onClear} disabled={!searchInput && activeFilterCount === 0}>清除搜索与筛选</Button>
      </div>
    </div>}
  </div>;
}

export function AdminDocumentsPage({ embedded = false }: { embedded?: boolean }) {
  const { state } = useAuth();
  const canPublish = state.status === "authed"
    && (state.user.role === "admin" || state.user.content_permissions?.includes("item.publish"));
  const [listing, setListing] = useState<ManagedIndexJobList>(EMPTY_LIST);
  const [categories, setCategories] = useState<ManagedCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [docType, setDocType] = useState("");
  const [sourceOrigin, setSourceOrigin] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [history, setHistory] = useState(false);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [retryingJobId, setRetryingJobId] = useState<string | null>(null);
  const [cancelTarget, setCancelTarget] = useState<ManagedIndexJob & Partial<UnifiedPublicationJob> | null>(null);
  const [cancellingJobId, setCancellingJobId] = useState<string | null>(null);
  const [selectedJobIds, setSelectedJobIds] = useState<string[]>([]);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkMenuOpen, setBulkMenuOpen] = useState(false);
  const bulkMenuRef = useRef<HTMLDivElement | null>(null);
  const bulkMenuTriggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(searchInput.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    setPage(0);
    setSelectedJobIds([]);
  }, [query, categoryId, docType, sourceOrigin, status, history, includeArchived, pageSize]);

  useEffect(() => {
    setSelectedJobIds((current) => current.filter((id) => listing.jobs.some((job) => job.id === id && job.is_latest_attempt && !job.is_archived)));
  }, [listing.jobs]);

  useEffect(() => {
    if (selectedJobIds.length === 0) setBulkMenuOpen(false);
  }, [selectedJobIds.length]);

  useEffect(() => {
    if (!bulkMenuOpen) return;
    const closeOutside = (event: MouseEvent) => {
      if (!bulkMenuRef.current?.contains(event.target as Node)) setBulkMenuOpen(false);
    };
    const closeEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setBulkMenuOpen(false);
      bulkMenuTriggerRef.current?.focus();
    };
    document.addEventListener("mousedown", closeOutside);
    document.addEventListener("keydown", closeEscape);
    return () => {
      document.removeEventListener("mousedown", closeOutside);
      document.removeEventListener("keydown", closeEscape);
    };
  }, [bulkMenuOpen]);

  const params = useMemo(() => ({
    query: query || undefined,
    category_id: categoryId || undefined,
    doc_type: docType || undefined,
    source_origin: sourceOrigin || undefined,
    status: status === "all" ? undefined : status === "ready" ? "published" : status,
    history,
    include_archived: includeArchived,
    limit: pageSize,
    offset: page * pageSize,
  }), [query, categoryId, docType, sourceOrigin, status, history, includeArchived, page, pageSize]);

  const load = useCallback(async (background = false) => {
    background ? setRefreshing(true) : setLoading(true);
    setError(null);
    try {
    const unified = await adminContentApi.publicationJobs(params);
    setListing({
      ...unified,
      status_counts: { processing: unified.status_counts.processing || 0, ready: unified.status_counts.published || 0, failed: unified.status_counts.failed || 0 },
      jobs: unified.jobs.map((job) => ({
        ...job,
        status: job.status === "published" ? "done" : job.status === "processing" ? "pending" : "failed",
        publication_id: job.publication_id || `video:${job.id}`,
        is_archived: job.is_archived,
        is_current_head: job.is_current_head,
        is_latest_attempt: job.is_latest_attempt,
        failure: job.error_code ? { code: job.error_code, message: job.error_summary || job.error_code, retryable: job.retryable } : null,
      })) as unknown as ManagedIndexJob[],
    });
    } catch (caught: any) {
      setError(caught?.message || String(caught));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [params]);

  useEffect(() => {
    adminContentApi.categories(false)
      .then(setCategories)
      .catch((caught: any) => setError(caught?.message || String(caught)));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const hasActive = listing.jobs.some((job) => ACTIVE_STATUSES.has(job.status));
  useManagedContentLiveRefresh({ active: hasActive, refresh: () => load(true) });

  const counts = listing.status_counts;
  const allCount = Object.values(counts).reduce((sum, value) => sum + value, 0);
  const pageCount = Math.max(1, Math.ceil(listing.total / pageSize));
  const hasFilters = Boolean(query || categoryId || docType || sourceOrigin || status !== "all" || history || includeArchived);
  const listingScopeDescription = `${history ? "正在显示全部历史尝试。" : "每个资料版本仅显示最新一次发布尝试。"} ${includeArchived ? "已包含回收站资料。" : "默认不显示回收站资料。"}`;

  const clearFilters = () => {
    setSearchInput("");
    setQuery("");
    setCategoryId("");
    setDocType("");
    setSourceOrigin("");
    setStatus("all");
    setHistory(false);
    setIncludeArchived(false);
  };

  const retryPublication = async (job: ManagedIndexJob) => {
    if (!canPublish || retryingJobId || job.is_archived || !["failed", "done"].includes(job.status) || !job.is_latest_attempt) return;
    setRetryingJobId(job.id);
    try {
      if ((job as ManagedIndexJob & { task_type?: string }).task_type === "video_transcript") {
        await adminContentApi.retryPublicationJob(job.id, "video_transcript");
      } else {
        await adminContentApi.publish(job.version_id);
      }
      toast.success("已重新加入发布队列");
      await load(true);
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : "重新发布失败");
    } finally {
      setRetryingJobId(null);
    }
  };

  const cancelPublication = async () => {
    if (!cancelTarget || cancellingJobId) return;
    setCancellingJobId(cancelTarget.id);
    try {
      await adminContentApi.cancelPublicationJob(cancelTarget.id, "video_transcript");
      toast.success("已取消发布，视频已恢复待发布状态");
      setCancelTarget(null);
      await load(true);
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : "取消发布失败");
    } finally {
      setCancellingJobId(null);
    }
  };

  const selectableJobs = listing.jobs.filter((job) => job.is_latest_attempt && !job.is_archived);
  const actionableJobs = listing.jobs.filter((job) => selectedJobIds.includes(job.id) && ["failed", "done"].includes(job.status) && job.is_latest_attempt && !job.is_archived);
  const toggleJob = (id: string) => setSelectedJobIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const togglePage = (checked: boolean) => {
    const ids = selectableJobs.map((job) => job.id);
    setSelectedJobIds((current) => checked
      ? [...new Set([...current, ...ids])]
      : current.filter((id) => !ids.includes(id)));
  };
  const bulkRepublish = async () => {
    const jobs = actionableJobs;
    if (!jobs.length || bulkBusy) return;
    setBulkBusy(true);
    try {
      const result = await adminContentApi.bulkPublish(jobs.map((job) => job.version_id));
      const failed = result.results.filter((item) => item.status === "failed");
      setSelectedJobIds(jobs.filter((job) => failed.some((item) => item.version_id === job.version_id)).map((job) => job.id));
      const skipped = selectedJobIds.length - jobs.length;
      toast.success(failed.length || skipped ? `已提交 ${result.succeeded} 个任务，${failed.length + skipped} 个未处理` : `已提交 ${result.succeeded} 个任务`);
      await load(true);
    } catch (caught) { toast.error(caught instanceof Error ? caught.message : "批量重新发布失败"); }
    finally { setBulkBusy(false); }
  };
  const exportFailures = () => {
    const failures = listing.jobs.filter((job) => (selectedJobIds.length ? selectedJobIds.includes(job.id) : true) && job.status === "failed");
    const csv = ["资料名称,文件名,版本,失败时间,失败原因,建议操作", ...failures.map((job) => [job.title || "", job.original_filename || "", job.version_number || "", formatAdminDate(job.updated_at), job.failure?.message || job.error_summary || "", job.failure?.recommended_action || ""].map((value) => `"${String(value).replaceAll('"', '""')}"`).join(","))].join("\r\n");
    const url = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = "索引失败报告.csv"; anchor.click(); URL.revokeObjectURL(url);
    setBulkMenuOpen(false);
  };

  const titleId = embedded ? "managed-index-view-title" : "admin-documents-title";

  return (
      <section className="space-y-5" aria-labelledby={titleId}>
      {!embedded && <header><p className="text-ui-xs font-medium text-primary">内容管理</p><h1 id={titleId} className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">发布任务</h1><p className="mt-1 max-w-3xl text-ui-sm text-muted-foreground">统一跟踪普通资料和视频转录稿的发布处理状态，并处理可重试的失败任务。</p></header>}

      {error && (
        <ErrorState
          title="发布任务加载失败"
          description={error}
          action={<Button variant="outline" size="sm" onClick={() => void load()}>重新加载</Button>}
        />
      )}

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="发布任务状态概览">
        <ManagedSummaryCard label="全部任务" value={allCount} icon={<ListChecks className="size-4" />} active={status === "all"} onClick={() => setStatus("all")} />
        <ManagedSummaryCard label="处理中" value={counts.processing || 0} icon={<Clock3 className="size-4" />} tone="warning" active={status === "processing"} onClick={() => setStatus((current) => current === "processing" ? "all" : "processing")} />
        <ManagedSummaryCard label="已发布" value={counts.ready || 0} icon={<CheckCircle2 className="size-4" />} tone="success" active={status === "ready"} onClick={() => setStatus((current) => current === "ready" ? "all" : "ready")} />
        <ManagedSummaryCard label="发布失败" value={counts.failed || 0} icon={<CircleAlert className="size-4" />} tone="destructive" active={status === "failed"} onClick={() => setStatus((current) => current === "failed" ? "all" : "failed")} />
      </section>

      <Card className="overflow-hidden shadow-surface" aria-labelledby={embedded ? titleId : "managed-index-title"}>
        <div className="grid gap-3 border-b border-border px-4 py-4 sm:px-5 xl:grid-cols-[minmax(13rem,1fr)_18rem_auto] xl:items-end min-[1400px]:grid-cols-[minmax(13rem,1fr)_24rem_auto]">
          <div>
            {embedded
              ? <h2 id={titleId} className="text-ui-base font-semibold text-foreground">发布任务</h2>
              : <h2 id="managed-index-title" className="text-ui-base font-semibold text-foreground">资料发布任务</h2>}
            <p className="mt-1 text-ui-xs text-muted-foreground">
              {embedded ? "跟踪资料版本的发布处理状态，并处理可重试的失败任务。" : listingScopeDescription}
            </p>
          </div>
          <IndexTaskSearchFilters searchInput={searchInput} categoryId={categoryId} docType={docType} sourceOrigin={sourceOrigin} status={status} history={history} includeArchived={includeArchived} categories={categories} onSearchInputChange={setSearchInput} onCategoryChange={setCategoryId} onDocTypeChange={setDocType} onSourceChange={setSourceOrigin} onStatusChange={setStatus} onHistoryChange={setHistory} onIncludeArchivedChange={setIncludeArchived} onClear={clearFilters} />
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button variant="outline" onClick={() => void load(true)} disabled={loading || refreshing}>
              <RefreshCw className={cn("size-4", refreshing && "animate-spin")} />
              {refreshing ? "刷新中…" : "刷新列表"}
            </Button>
            <div ref={bulkMenuRef} className="relative">
              <Button
                ref={bulkMenuTriggerRef}
                variant="outline"
                aria-haspopup="menu"
                aria-expanded={bulkMenuOpen}
                disabled={!canPublish || bulkBusy || selectedJobIds.length === 0}
                onClick={() => setBulkMenuOpen((current) => !current)}
              >
                批量操作{selectedJobIds.length > 0 ? `（${selectedJobIds.length}）` : ""}
                <ChevronDown className="size-4" />
              </Button>
              {bulkMenuOpen && (
                <div role="menu" aria-label="发布任务批量操作" className="absolute right-0 top-full z-dropdown mt-2 min-w-44 rounded-ui-md border border-border bg-popover p-1 text-popover-foreground shadow-overlay">
                  <button type="button" role="menuitem" className="flex w-full items-center gap-2 rounded-ui-sm px-3 py-2 text-left text-ui-sm hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-50" disabled={actionableJobs.length === 0 || bulkBusy} onClick={() => { setBulkMenuOpen(false); void bulkRepublish(); }}>
                    <Rocket className="size-4" />
                    批量重新发布
                  </button>
                  <button type="button" role="menuitem" className="flex w-full items-center gap-2 rounded-ui-sm px-3 py-2 text-left text-ui-sm hover:bg-surface-muted" onClick={exportFailures}>
                    <Download className="size-4" />
                    导出失败报告
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>

        {loading ? (
          <LoadingState className="min-h-64 rounded-none border-x-0 border-b-0" label="正在加载发布任务…" />
        ) : listing.jobs.length === 0 ? (
          <EmptyState
            className="rounded-none border-x-0 border-b-0"
            title={hasFilters ? "没有符合条件的发布任务" : "暂无发布任务"}
            description={hasFilters ? "请调整搜索或筛选条件。" : "资料在资料管理中发布后，处理状态会显示在这里。"}
          />
        ) : (
          <ManagedJobsTable
            jobs={listing.jobs}
            history={history}
            selectedJobIds={selectedJobIds}
            selectableJobs={selectableJobs}
            onToggleJob={toggleJob}
            onTogglePage={togglePage}
            retryingJobId={retryingJobId}
            canPublish={Boolean(canPublish)}
            onRetry={retryPublication}
            onCancel={setCancelTarget}
            cancellingJobId={cancellingJobId}
          />
        )}

        {!loading && listing.total > 0 && (
          <div className="flex flex-col gap-2 border-t border-border px-4 py-3 text-ui-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-5">
            <span>共 {listing.total} 条任务，第 {page + 1} / {pageCount} 页</span>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <label className="flex items-center gap-2 text-ui-xs text-muted-foreground">每页<Select aria-label="每页发布任务条数" className="h-control-sm w-20" value={String(pageSize)} onChange={(event) => setPageSize(Number(event.target.value))}>{PAGE_SIZE_OPTIONS.map((size) => <option key={size} value={size}>{size} 条</option>)}</Select></label>
              <Button size="sm" variant="outline" disabled={page === 0 || loading} onClick={() => setPage((value) => value - 1)}>上一页</Button>
              <Select aria-label="跳转发布任务页码" className="h-control-sm w-24" value={String(page + 1)} onChange={(event) => setPage(Number(event.target.value) - 1)} disabled={loading}>{Array.from({ length: pageCount }, (_, index) => <option key={index + 1} value={index + 1}>第 {index + 1} 页</option>)}</Select>
              <Button size="sm" variant="outline" disabled={page + 1 >= pageCount || loading} onClick={() => setPage((value) => value + 1)}>下一页</Button>
            </div>
          </div>
        )}
      </Card>

      <Dialog open={cancelTarget != null} onOpenChange={(open) => { if (!open && !cancellingJobId) setCancelTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>取消发布该视频？</DialogTitle>
            <DialogDescription>“{cancelTarget?.title || cancelTarget?.original_filename || "该视频"}”将恢复为待发布状态，从发布任务列表中移除；进行中的转录任务会一并取消。已发布的视频不受影响，历史记录会保留。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" disabled={Boolean(cancellingJobId)} onClick={() => setCancelTarget(null)}>取消</Button>
            <Button variant="destructive" disabled={Boolean(cancellingJobId)} onClick={() => void cancelPublication()}>{cancellingJobId ? "取消发布中…" : "确认取消发布"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
function ManagedJobsTable({
  jobs,
  history,
  retryingJobId,
  canPublish,
  onRetry,
  selectedJobIds,
  selectableJobs,
  onToggleJob,
  onTogglePage,
  onCancel,
  cancellingJobId,
}: {
  jobs: ManagedIndexJob[];
  history: boolean;
  retryingJobId: string | null;
  canPublish: boolean;
  onRetry: (job: ManagedIndexJob) => void;
  selectedJobIds: string[];
  selectableJobs: ManagedIndexJob[];
  onToggleJob: (id: string) => void;
  onTogglePage: (checked: boolean) => void;
  onCancel: (job: ManagedIndexJob & Partial<UnifiedPublicationJob>) => void;
  cancellingJobId: string | null;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="block w-full text-ui-sm lg:table lg:min-w-[68rem]">
        <caption className="sr-only">资料发布任务、类型、更新时间、状态、来源、内容块和操作</caption>
        <thead className="hidden border-b border-border bg-surface-muted text-left text-muted-foreground lg:table-header-group">
          <tr>
            <th className="w-12 px-3 py-3"><Checkbox aria-label="全选当前页索引任务" checked={selectableJobs.length > 0 && selectableJobs.every((job) => selectedJobIds.includes(job.id))} onChange={(event) => onTogglePage(event.target.checked)} /></th>
            <th className="w-16 px-2 py-3 text-center font-medium">类型</th>
            <th className="min-w-48 px-3 py-3 font-medium">资料</th>
            <th className="min-w-24 whitespace-nowrap px-3 py-3 font-medium">更新时间</th>
            <th className="min-w-48 px-3 py-3 font-medium">状态</th>
            <th className="w-24 whitespace-nowrap px-3 py-3 font-medium">来源</th>
            <th className="w-32 whitespace-nowrap px-3 py-3 text-right font-medium">操作</th>
          </tr>
        </thead>
        <tbody className="block divide-y divide-border lg:table-row-group">
          {jobs.map((job) => {
            const publicationJob = job as ManagedIndexJob & Partial<UnifiedPublicationJob>;
            const isVideoIntent = publicationJob.task_type === "video_transcript"
              && Boolean(publicationJob.media_id)
              && Boolean(publicationJob.transcription_action);
            const transcriptionHref = publicationJob.transcription_action === "start_transcription"
              ? `/admin/content?view=transcription&media_id=${encodeURIComponent(publicationJob.media_id || "")}&action=start-transcription`
              : publicationJob.transcription_action === "open_transcript_workbench"
                ? `/admin/content?view=transcription&media_id=${encodeURIComponent(publicationJob.media_id || "")}&workbench=1`
                : `/admin/content?view=transcription&media_id=${encodeURIComponent(publicationJob.media_id || "")}&task=1`;
            const meta = STATUS_META[job.status] || { label: job.status, hint: "状态待确认", variant: "secondary" as const };
            const statusHint = job.is_archived
              ? "资料已移入回收站，不参与知识库检索"
              : job.status === "done"
              ? job.is_current_head ? "当前正式版本可检索" : "非当前正式版本"
              : meta.hint;
            const retrying = retryingJobId === job.id;
            return (
              <tr key={job.id} className="group grid grid-cols-[2.5rem_5rem_minmax(0,1fr)] gap-x-2 gap-y-3 px-4 py-4 transition-colors duration-normal hover:bg-surface-muted/60 sm:px-5 lg:table-row lg:p-0">
                <td className="flex items-start justify-center px-1 pt-0 lg:table-cell lg:w-12 lg:px-3 lg:py-3 lg:align-top"><Checkbox aria-label={`选择${job.title || job.original_filename || "任务"}`} checked={selectedJobIds.includes(job.id)} disabled={!(job.is_latest_attempt && !job.is_archived) && !selectedJobIds.includes(job.id)} onChange={() => onToggleJob(job.id)} /></td>
                <td className="block lg:table-cell lg:px-2 lg:py-3"><ManagedItemType docType={job.doc_type} /></td>
                <td className="block min-w-0 lg:table-cell lg:px-3 lg:py-3">
                  <p className="break-words font-medium text-foreground" title={job.title || job.original_filename || undefined}>
                    {job.title || job.original_filename || "未命名资料"}
                  </p>
                  <p className="mt-1 break-all text-ui-xs text-muted-foreground">
                    {[job.original_filename || "—", job.version_number ? `v${job.version_number}` : null, job.file_size != null ? formatBytes(job.file_size) : null].filter(Boolean).join(" · ")}
                  </p>
                  {job.attempt_count > 1 && (
                    <p className="mt-1 text-ui-xs text-muted-foreground">
                      共尝试 {job.attempt_count} 次{history ? ` · 当前第 ${job.attempt_number} 次` : ""}
                    </p>
                  )}
                  <p className="mt-1 break-words text-ui-xs text-muted-foreground">分类：{job.category_path || job.category_label || "—"}</p>
                </td>
                <td className="col-span-2 block text-ui-xs text-muted-foreground lg:table-cell lg:whitespace-nowrap lg:px-3 lg:py-3">
                  <span className="lg:hidden">更新时间： </span>{formatAdminDate(job.updated_at)}
                </td>
                <td className="col-span-2 block lg:table-cell lg:px-3 lg:py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={meta.variant}>{meta.label}</Badge>
                    {job.is_archived && <Badge variant="secondary">已下架</Badge>}
                  </div>
                  <p className="mt-1 text-ui-xs text-muted-foreground">{statusHint}</p>
                  {job.failure && (
                    <div className="mt-2 max-w-md space-y-1 text-ui-xs">
                      <p className="break-words text-destructive">{job.failure.message}</p>
                      <p className="break-words text-muted-foreground">{job.failure.recommended_action}</p>
                      <p className="text-muted-foreground">{job.failure.retryable ? "可以重新发布" : "请先处理文件或系统配置"}</p>
                    </div>
                  )}
                </td>
                <td className="col-span-2 block text-ui-xs text-muted-foreground lg:table-cell lg:whitespace-nowrap lg:px-3 lg:py-3 lg:text-ui-sm lg:text-foreground">
                  <span className="lg:hidden">来源： </span>{sourceOriginLabel(job.source_origin)}
                  <span className="mt-1 block text-ui-xs text-muted-foreground">内容块：{job.status === "done" && job.is_current_head && job.parent_count != null ? `${job.parent_count} 个` : "—"}</span>
                </td>
                <td className="col-span-2 flex flex-wrap justify-end gap-2 lg:table-cell lg:px-3 lg:py-3">
                  <div className="flex flex-wrap justify-end gap-2">
                    {isVideoIntent ? (
                      <a
                        className={cn(buttonVariants({ variant: "outline", size: "sm" }), "max-sm:h-control-md")}
                        href={transcriptionHref}
                        aria-label={`转录“${job.title || job.original_filename || "视频"}”`}
                      >
                        <Rocket className="size-4" />
                        转录
                      </a>
                    ) : (
                      <a
                        className={cn(buttonVariants({ variant: "outline", size: "sm" }), "max-sm:h-control-md")}
                        href={adminContentApi.fileUrl(job.version_id)}
                        target="_blank"
                        rel="noreferrer"
                      >
                        <Eye className="size-4" />
                        查看文件
                      </a>
                    )}
                    {isVideoIntent && publicationJob.cancelable && (
                      <Button
                        size="sm"
                        variant="outline"
                        className="max-sm:h-control-md"
                        disabled={Boolean(cancellingJobId)}
                        title="取消本次发布，视频恢复待发布状态"
                        onClick={() => onCancel(publicationJob)}
                      >
                        <Ban className="size-4" />
                        取消发布
                      </Button>
                    )}
                    <Button
                      size="sm"
                      className="max-sm:h-control-md"
                      disabled={!canPublish || Boolean(retryingJobId) || job.is_archived || !["failed", "done"].includes(job.status) || !job.is_latest_attempt}
                      title={!canPublish ? "当前账号没有发布权限" : job.is_archived ? "回收站资料不可重试" : !["failed", "done"].includes(job.status) ? "仅失败或已发布任务可重新发布" : !job.is_latest_attempt ? "仅最新尝试可重试" : "重新发布"}
                      onClick={() => onRetry(job)}
                    >
                      <Rocket className={cn("size-4", retrying && "animate-pulse")} />
                      {retrying ? "发布中…" : "重新发布"}
                    </Button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}


