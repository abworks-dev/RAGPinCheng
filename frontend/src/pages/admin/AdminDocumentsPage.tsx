import {
  Activity,
  ChevronDown,
  Eye,
  FileText,
  MoreHorizontal,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  Video,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../../api/client";
import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { Input } from "../../components/ui/input";
import { LoadingState } from "../../components/ui/loading-state";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "../../components/ui/sheet";
import type {
  CategoryTree,
  IndexJob,
  IndexedDocument,
  IndexedDocumentList,
  ManagedIndexJob,
} from "../../types";
import { cn } from "../../lib/utils";
import { formatAdminDate, formatBytes } from "./admin-formatters";
import { PdfPreview } from "../../components/PdfPreview";
import { PdfPreviewProvider, usePdfPreview } from "../../hooks/usePdfPreview";
import { useVideoPlayer } from "../../hooks/useVideoPlayer";

const NEW_CATEGORY_SENTINEL = "__new__";
const PAGE_SIZE = 25;
const ACTIVE_STATUSES = new Set([
  "pending",
  "uploading",
  "queued_mineru",
  "parsing",
  "chunking",
  "summarizing",
  "embedding",
]);
const SUPPORTED_EXTENSIONS = [".pdf", ".md", ".docx", ".xlsx", ".pptx"];

type StatusVariant = "secondary" | "success" | "warning" | "destructive" | "info";
type StatusFilter = "all" | "processing" | "ready" | "failed";

const STATUS_META: Record<string, { label: string; hint?: string; variant: StatusVariant }> = {
  pending: { label: "排队中", hint: "等待处理", variant: "secondary" },
  uploading: { label: "上传中", hint: "正在上传文件", variant: "info" },
  queued_mineru: { label: "等待解析", hint: "等待解析器资源", variant: "warning" },
  parsing: { label: "解析中", hint: "正在提取文档内容", variant: "info" },
  chunking: { label: "切分中", hint: "正在生成可检索内容块", variant: "info" },
  summarizing: { label: "生成摘要", hint: "正在处理表格摘要", variant: "warning" },
  embedding: { label: "写入索引", hint: "正在写入向量索引", variant: "warning" },
  done: { label: "可检索", hint: "资料已进入知识库", variant: "success" },
  failed: { label: "处理失败", hint: "可以重试", variant: "destructive" },
};

const selectClassName =
  "h-control-md w-full rounded-ui-md border border-input bg-background px-3 text-ui-sm text-foreground shadow-sm outline-none transition-colors duration-normal focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50";

const emptyDocumentList: IndexedDocumentList = {
  documents: [],
  total: 0,
  status_counts: {},
};

function useElapsed(startTs: number | null | undefined): string {
  const [now, setNow] = useState(() => Date.now());
  const active = startTs != null;

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);

  if (!startTs) return "";
  const seconds = Math.max(0, Math.floor((now - startTs * 1000) / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m${seconds % 60}s`;
}

function documentTypeLabel(document: Pick<IndexedDocument, "doc_type" | "filename">): string {
  if (document.doc_type === "transcript") return "视频转写";
  if (document.doc_type === "docx") return "Word";
  if (document.doc_type === "xlsx") return "Excel";
  if (document.doc_type === "pptx") return "PPT";
  if (document.filename.toLowerCase().endsWith(".md")) return "Markdown";
  return "PDF";
}

function DocumentStatus({ document }: { document: IndexedDocument }) {
  const meta = STATUS_META[document.status] || {
    label: document.status,
    variant: "secondary" as const,
  };
  return (
    <div>
      <Badge variant={meta.variant}>{meta.label}</Badge>
      <p className="mt-1 text-ui-xs text-muted-foreground">
        {document.error_summary || meta.hint || "状态待确认"}
      </p>
      {document.status === "failed" && document.is_indexed && (
        <p className="mt-1 text-ui-xs text-warning">已有索引仍保留，建议检查后重试。</p>
      )}
    </div>
  );
}

export function AdminDocumentsPage() {
  return (
    <PdfPreviewProvider>
      <AdminDocumentsPageContent />
      <PdfPreview />
    </PdfPreviewProvider>
  );
}

function AdminDocumentsPageContent() {
  const [tree, setTree] = useState<CategoryTree | null>(null);
  const [listing, setListing] = useState<IndexedDocumentList>(emptyDocumentList);
  const [jobs, setJobs] = useState<IndexJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [jobsLoading, setJobsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [managedJobs, setManagedJobs] = useState<ManagedIndexJob[]>([]);
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [docType, setDocType] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [page, setPage] = useState(0);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(searchInput.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    setPage(0);
  }, [query, category, docType, statusFilter]);

  const documentParams = useMemo(
    () => ({
      query: query || undefined,
      category: category || undefined,
      doc_type: docType || undefined,
      status: statusFilter === "all" ? undefined : statusFilter,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    }),
    [query, category, docType, statusFilter, page],
  );

  const refreshDocuments = useCallback(async () => {
    setLoading(true);
    try {
      setListing(await api.adminListIndexedDocuments(documentParams));
    } finally {
      setLoading(false);
    }
  }, [documentParams]);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    setJobsLoading(true);
    setError(null);
    try {
      const [categoryTree, indexedDocuments, indexJobs, managedIndexJobs] = await Promise.all([
        api.adminCategoryTree(),
        api.adminListIndexedDocuments(documentParams),
        api.adminListIndexJobs(100),
        api.managedContentIndexJobs({ limit: 100 }),
      ]);
      setTree(categoryTree);
      setListing(indexedDocuments);
      setJobs(indexJobs.jobs);
      setManagedJobs(managedIndexJobs.jobs);
    } catch (caught: any) {
      setError(caught?.message || String(caught));
    } finally {
      setLoading(false);
      setJobsLoading(false);
    }
  }, [documentParams]);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  const hasActive = jobs.some((job) => ACTIVE_STATUSES.has(job.status)) || managedJobs.some((job) => ACTIVE_STATUSES.has(job.status));
  useEffect(() => {
    if (!hasActive) return;
    const timer = window.setInterval(async () => {
      try {
        const [legacy, managed] = await Promise.all([api.adminListIndexJobs(100), api.managedContentIndexJobs({ limit: 100 })]);
        setJobs(legacy.jobs);
        setManagedJobs(managed.jobs);
        await refreshDocuments();
      } catch {
        // Best-effort polling keeps the last usable state and retries next tick.
      }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [hasActive, refreshDocuments]);

  const pageCount = Math.max(1, Math.ceil(listing.total / PAGE_SIZE));
  const categoryNames = tree?.categories.map((item) => item.name) || [];
  const summary = listing.status_counts;

  return (
    <section className="space-y-5" aria-labelledby="admin-documents-title">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-ui-xs font-medium uppercase tracking-[0.14em] text-primary">知识库维护</p>
          <h1 id="admin-documents-title" className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">
            索引监控
          </h1>
          <p className="mt-1 max-w-3xl text-ui-sm text-muted-foreground">
            查看新资料发布和旧目录索引状态；新资料请从“资料库”上传。
          </p>
        </div>
      </header>

      {error && (
        <ErrorState
          title="资料数据加载失败"
          description={error}
          action={<Button variant="outline" size="sm" onClick={() => void refreshAll()}>重新加载</Button>}
        />
      )}

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="资料状态概览">
        <SummaryCard label="全部资料" value={Object.values(summary).reduce((sum, value) => sum + value, 0)} />
        <SummaryCard label="处理中" value={summary.processing || 0} tone="warning" />
        <SummaryCard label="可检索" value={summary.ready || 0} tone="success" />
        <SummaryCard label="处理失败" value={summary.failed || 0} tone="destructive" />
      </section>

      <section className="space-y-3" aria-labelledby="document-list-title">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <h2 id="document-list-title" className="text-ui-base font-semibold text-foreground">旧索引资料</h2>
            <p className="mt-1 text-ui-xs text-muted-foreground">分类来自旧 docs 目录，仅供兼容期查看，不再作为新资料分类来源。</p>
          </div>
          <div className="grid gap-2 sm:grid-cols-2 xl:flex xl:items-center">
            <label className="relative block sm:col-span-2 xl:w-72">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <span className="sr-only">搜索资料</span>
              <Input
                type="search"
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                placeholder="搜索标题、文件名或分类…"
                className="pl-9"
              />
            </label>
            <select
              aria-label="按分类筛选"
              value={category}
              onChange={(event) => setCategory(event.target.value)}
              className={cn(selectClassName, "xl:w-40")}
            >
              <option value="">全部分类</option>
              {categoryNames.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
            <select
              aria-label="按文件类型筛选"
              value={docType}
              onChange={(event) => setDocType(event.target.value)}
              className={cn(selectClassName, "xl:w-36")}
            >
              <option value="">全部类型</option>
              <option value="pdf">PDF / Markdown</option>
              <option value="docx">Word</option>
              <option value="xlsx">Excel</option>
              <option value="pptx">PPT</option>
              <option value="transcript">视频转写</option>
            </select>
          </div>
        </div>

        <div className="flex flex-wrap gap-2" aria-label="按处理状态筛选">
          {([
            ["all", "全部"],
            ["processing", "处理中"],
            ["ready", "可检索"],
            ["failed", "失败"],
          ] as const).map(([value, label]) => (
            <Button
              key={value}
              size="sm"
              variant={statusFilter === value ? "default" : "outline"}
              onClick={() => setStatusFilter(value)}
            >
              {label}
            </Button>
          ))}
        </div>

        {loading ? (
          <Card><LoadingState className="min-h-64" label="正在加载资料…" /></Card>
        ) : listing.documents.length === 0 ? (
          <EmptyState
            title={query || category || docType || statusFilter !== "all" ? "没有符合条件的资料" : "暂无资料"}
            description={query || category || docType || statusFilter !== "all" ? "请调整搜索或筛选条件。" : "上传第一份资料后，处理状态会显示在这里。"}
          />
        ) : (
          <DocumentsTable
            documents={listing.documents}
            onChanged={refreshAll}
          />
        )}

        {!loading && listing.total > 0 && (
          <div className="flex flex-col gap-2 text-ui-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
            <span>共 {listing.total} 份资料，第 {page + 1} / {pageCount} 页</span>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage((value) => value - 1)}>
                上一页
              </Button>
              <Button size="sm" variant="outline" disabled={page + 1 >= pageCount} onClick={() => setPage((value) => value + 1)}>
                下一页
              </Button>
            </div>
          </div>
        )}
      </section>

      <ManagedJobsActivity jobs={managedJobs} loading={jobsLoading} />
      <JobsActivity jobs={jobs} loading={jobsLoading} onChanged={refreshAll} />
    </section>
  );
}

function ManagedJobsActivity({ jobs, loading }: { jobs: ManagedIndexJob[]; loading: boolean }) {
  const label: Record<string, string> = { pending: "排队中", parsing: "解析中", chunking: "切分中", summarizing: "生成摘要", embedding: "写入索引", done: "已发布", failed: "发布失败" };
  return <section className="space-y-3 border-y border-border py-5" aria-labelledby="managed-index-title">
    <div><h2 id="managed-index-title" className="text-ui-base font-semibold">资料库发布任务</h2><p className="mt-1 text-ui-xs text-muted-foreground">来自数据库分类；发布成功后才成为正式可检索版本。</p></div>
    {loading ? <LoadingState className="min-h-32" label="正在加载发布任务…" /> : jobs.length === 0 ? <EmptyState title="暂无发布任务" description="资料在资料库中确认并发布后，任务会显示在这里。" /> : <div className="overflow-x-auto border border-border"><table className="w-full min-w-[48rem] text-ui-sm"><thead className="border-b border-border bg-surface-muted text-left text-muted-foreground"><tr><th className="px-4 py-3 font-medium">资料</th><th className="px-4 py-3 font-medium">数据库分类</th><th className="px-4 py-3 font-medium">状态</th><th className="px-4 py-3 font-medium">更新时间</th></tr></thead><tbody className="divide-y divide-border">{jobs.map((job) => <tr key={job.id}><td className="px-4 py-3"><p className="font-medium">{job.title || job.original_filename}</p><p className="mt-1 break-all text-ui-xs text-muted-foreground">{job.original_filename}</p></td><td className="px-4 py-3">{job.category_label || "—"}</td><td className="px-4 py-3"><Badge variant={job.status === "done" ? "success" : job.status === "failed" ? "destructive" : "warning"}>{label[job.status] || job.status}</Badge>{job.error_summary && <p className="mt-1 text-ui-xs text-destructive">{job.error_summary}</p>}</td><td className="px-4 py-3 text-ui-xs text-muted-foreground">{formatAdminDate(job.updated_at)}</td></tr>)}</tbody></table></div>}
  </section>;
}

function SummaryCard({
  label,
  value,
  tone = "secondary",
}: {
  label: string;
  value: number;
  tone?: "secondary" | "warning" | "success" | "destructive";
}) {
  const dotClass = {
    secondary: "bg-muted-foreground",
    warning: "bg-warning",
    success: "bg-success",
    destructive: "bg-destructive",
  }[tone];
  return (
    <Card className="flex items-center justify-between px-4 py-3 shadow-surface">
      <div className="flex items-center gap-2 text-ui-sm text-muted-foreground">
        <span className={cn("size-2 rounded-full", dotClass)} />
        {label}
      </div>
      <strong className="text-ui-xl tabular-nums text-foreground">{value}</strong>
    </Card>
  );
}

function DocumentsTable({
  documents,
  onChanged,
}: {
  documents: IndexedDocument[];
  onChanged: () => Promise<void> | void;
}) {
  const [deleteTarget, setDeleteTarget] = useState<IndexedDocument | null>(null);
  const [deleteFile, setDeleteFile] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deletePartiallyCompleted, setDeletePartiallyCompleted] = useState(false);
  const [retryingId, setRetryingId] = useState<number | null>(null);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [menuPosition, setMenuPosition] = useState({ top: 0, right: 0 });
  const menuRef = useRef<HTMLDivElement | null>(null);
  const triggerRefs = useRef(new Map<string, HTMLButtonElement>());
  const { open: openPreview } = usePdfPreview();
  const { open: openVideo } = useVideoPlayer();

  const openDocument = openMenuId
    ? documents.find((document) => document.document_id === openMenuId) ?? null
    : null;

  const closeMenu = useCallback((restoreFocus = false) => {
    const trigger = openMenuId ? triggerRefs.current.get(openMenuId) : null;
    setOpenMenuId(null);
    if (restoreFocus) requestAnimationFrame(() => trigger?.focus());
  }, [openMenuId]);

  const openMenu = useCallback((documentId: string, trigger: HTMLButtonElement) => {
    if (openMenuId === documentId) {
      closeMenu(true);
      return;
    }
    const rect = trigger.getBoundingClientRect();
    const menuHeight = 152;
    const top = rect.bottom + 4 + menuHeight <= window.innerHeight
      ? rect.bottom + 4
      : Math.max(8, rect.top - menuHeight - 4);
    setMenuPosition({ top, right: Math.max(8, window.innerWidth - rect.right) });
    setOpenMenuId(documentId);
  }, [closeMenu, openMenuId]);

  useEffect(() => {
    function closeOnOutsidePointer(event: PointerEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      const inMenu = menuRef.current?.contains(target);
      const inTrigger = Array.from(triggerRefs.current.values()).some((trigger) => trigger.contains(target));
      if (!inMenu && !inTrigger) setOpenMenuId(null);
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape" && openMenuId) {
        event.preventDefault();
        closeMenu(true);
      }
    }
    function closeOnViewportChange() {
      setOpenMenuId(null);
    }
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", closeOnViewportChange);
    window.addEventListener("scroll", closeOnViewportChange, true);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", closeOnViewportChange);
      window.removeEventListener("scroll", closeOnViewportChange, true);
    };
  }, [closeMenu, openMenuId]);

  useEffect(() => {
    if (!openMenuId) return;
    requestAnimationFrame(() => menuRef.current?.querySelector<HTMLElement>("[role=menuitem]:not(:disabled)")?.focus());
  }, [openMenuId]);

  function handleMenuKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLElement>("[role=menuitem]:not(:disabled)") ?? []);
    if (!items.length) return;
    const current = items.indexOf(document.activeElement as HTMLElement);
    let next = current;
    if (event.key === "ArrowDown") next = (current + 1) % items.length;
    else if (event.key === "ArrowUp") next = (current - 1 + items.length) % items.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = items.length - 1;
    else return;
    event.preventDefault();
    items[next]?.focus();
  }

  function closeDeleteDialog() {
    setDeleteTarget(null);
    setDeleteFile(false);
    setDeleteError(null);
    if (deletePartiallyCompleted) void onChanged();
    setDeletePartiallyCompleted(false);
  }

  async function retry(document: IndexedDocument) {
    if (document.latest_job_id == null) return;
    setRetryingId(document.latest_job_id);
    try {
      await api.adminRetryIndexJob(document.latest_job_id);
      await onChanged();
    } finally {
      setRetryingId(null);
    }
  }

  async function removeDocument() {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    setDeletePartiallyCompleted(false);
    try {
      const result = await api.adminDeleteIndexedDocument(deleteTarget.document_id, deleteFile);
      if (deleteFile && result.file_delete_status === "failed") {
        setDeletePartiallyCompleted(true);
        setDeleteError("知识库索引已移除，但源文件删除失败。请检查文件权限或占用情况后重试。");
        return;
      }
      setDeleteTarget(null);
      setDeleteFile(false);
      await onChanged();
    } catch (caught: any) {
      setDeleteError(caught?.message || String(caught));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <>
      <Card className="overflow-hidden shadow-surface">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[58rem] text-ui-sm">
            <caption className="sr-only">资料名称、分类、类型、状态、内容规模、更新时间和操作</caption>
            <thead className="border-b border-border bg-surface-muted text-muted-foreground">
              <tr>
                <th scope="col" className="px-4 py-3 text-left font-medium">资料</th>
                <th scope="col" className="px-4 py-3 text-left font-medium">分类</th>
                <th scope="col" className="px-4 py-3 text-left font-medium">类型</th>
                <th scope="col" className="px-4 py-3 text-left font-medium">当前状态</th>
                <th scope="col" className="px-4 py-3 text-right font-medium">内容块</th>
                <th scope="col" className="px-4 py-3 text-left font-medium">更新时间</th>
                <th scope="col" className="sticky right-0 bg-surface-muted px-3 py-3 text-right font-medium shadow-[-1px_0_0_rgb(var(--border))]">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {documents.map((document) => (
                <tr key={document.document_id} className="group bg-card align-top hover:bg-surface-muted/60">
                  <td className="px-4 py-3">
                    <div className="flex max-w-md gap-3">
                      <div className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-ui-md bg-secondary text-muted-foreground">
                        <FileText className="size-4" />
                      </div>
                      <div className="min-w-0">
                        <p className="truncate font-medium text-foreground" title={document.doc_title}>{document.doc_title}</p>
                        <p className="mt-1 truncate text-ui-xs text-muted-foreground" title={document.filename}>
                          {document.filename}
                          {document.file_size != null ? ` · ${formatBytes(document.file_size)}` : ""}
                        </p>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant="secondary">{document.category}</Badge>
                    {document.company && <p className="mt-1 text-ui-xs text-muted-foreground">{document.company}</p>}
                  </td>
                  <td className="px-4 py-3"><Badge variant="outline">{documentTypeLabel(document)}</Badge></td>
                  <td className="px-4 py-3"><DocumentStatus document={document} /></td>
                  <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                    {document.is_indexed ? document.parent_count : "—"}
                  </td>
                  <td className="px-4 py-3 text-ui-xs text-muted-foreground">
                    {document.updated_at ? formatAdminDate(document.updated_at) : "历史资料"}
                    {document.uploaded_by && <p className="mt-1">由 {document.uploaded_by} 上传</p>}
                  </td>
                  <td className="sticky right-0 bg-card px-3 py-3 text-right shadow-[-1px_0_0_rgb(var(--border))] group-hover:bg-surface-muted">
                    <div className="inline-block text-left">
                      <button
                        ref={(node) => {
                          if (node) triggerRefs.current.set(document.document_id, node);
                          else triggerRefs.current.delete(document.document_id);
                        }}
                        type="button"
                        onClick={(event) => openMenu(document.document_id, event.currentTarget)}
                        className="inline-flex size-9 items-center justify-center rounded-ui-md text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        aria-label={`打开 ${document.doc_title} 的操作菜单`}
                        aria-expanded={openMenuId === document.document_id}
                        aria-haspopup="menu"
                      >
                        <MoreHorizontal className="size-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {openDocument && createPortal(
        <div
          ref={menuRef}
          role="menu"
          aria-label={`${openDocument.doc_title}的资料操作`}
          onKeyDown={handleMenuKeyDown}
          className="fixed z-dropdown w-44 rounded-ui-md border border-border bg-popover p-1 text-left text-popover-foreground shadow-overlay"
          style={menuPosition}
        >
          {openDocument.preview_parent_id && ["pdf", "docx", "xlsx", "pptx"].includes(openDocument.doc_type) && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                closeMenu(true);
                openPreview(openDocument.preview_parent_id!, openDocument.doc_title, openDocument.doc_type, 1);
              }}
              className="flex w-full items-center gap-2 rounded-ui-sm px-3 py-2 text-ui-sm hover:bg-secondary"
            >
              <Eye className="size-4" />
              预览文件
            </button>
          )}
          {openDocument.doc_type === "transcript" && openDocument.media_id && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                closeMenu(true);
                openVideo({
                  mediaId: openDocument.media_id!,
                  title: openDocument.doc_title,
                  startSeconds: 0,
                  fromSource: false,
                });
              }}
              className="flex w-full items-center gap-2 rounded-ui-sm px-3 py-2 text-ui-sm hover:bg-secondary"
            >
              <Video className="size-4" />
              预览视频
            </button>
          )}
          {openDocument.latest_job_id != null && (openDocument.status === "failed" || openDocument.status === "done") && (
            <button
              type="button"
              role="menuitem"
              disabled={retryingId === openDocument.latest_job_id}
              onClick={() => { closeMenu(true); void retry(openDocument); }}
              className="flex w-full items-center gap-2 rounded-ui-sm px-3 py-2 text-ui-sm hover:bg-secondary disabled:opacity-50"
            >
              <RefreshCw className="size-4" />
              {openDocument.status === "failed" ? "重试处理" : "重新索引"}
            </button>
          )}
          {openDocument.is_indexed && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setDeleteError(null);
                setDeletePartiallyCompleted(false);
                setDeleteFile(false);
                setDeleteTarget(openDocument);
                closeMenu(true);
              }}
              className="flex w-full items-center gap-2 rounded-ui-sm px-3 py-2 text-ui-sm text-destructive hover:bg-destructive/10"
            >
              <Trash2 className="size-4" />
              移除资料
            </button>
          )}
        </div>,
        document.body,
      )}

      <Dialog open={deleteTarget != null} onOpenChange={(open) => !open && closeDeleteDialog()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>移除资料</DialogTitle>
            <DialogDescription>
              将“{deleteTarget?.doc_title}”从知识库索引中移除。默认保留源文件，之后仍可重新索引。
            </DialogDescription>
          </DialogHeader>
          <fieldset className="space-y-2">
            <legend className="mb-2 text-ui-sm font-medium text-foreground">源文件处理方式</legend>
            <label className="flex cursor-pointer gap-3 rounded-ui-md border border-border p-3">
              <input type="radio" name="delete-mode" checked={!deleteFile} onChange={() => setDeleteFile(false)} />
              <span><strong className="block text-ui-sm">保留源文件</strong><span className="text-ui-xs text-muted-foreground">仅移除检索索引，推荐选择。</span></span>
            </label>
            <label className="flex cursor-pointer gap-3 rounded-ui-md border border-destructive/40 p-3">
              <input type="radio" name="delete-mode" checked={deleteFile} onChange={() => setDeleteFile(true)} />
              <span><strong className="block text-ui-sm text-destructive">同时删除源文件</strong><span className="text-ui-xs text-muted-foreground">文件将无法通过重新索引恢复。</span></span>
            </label>
          </fieldset>
          {deleteError && <Alert variant="destructive" role="alert"><AlertTitle>删除未完全完成</AlertTitle><AlertDescription>{deleteError}</AlertDescription></Alert>}
          <DialogFooter>
            <Button variant="outline" onClick={closeDeleteDialog} disabled={deleting}>取消</Button>
            <Button variant="destructive" onClick={() => void removeDocument()} disabled={deleting}>
              {deleting ? "正在移除…" : deleteFile ? "删除资料和源文件" : "从知识库移除"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function UploadPanel({
  tree,
  onUploaded,
}: {
  tree: CategoryTree | null;
  onUploaded: () => Promise<void> | void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [pickedCategory, setPickedCategory] = useState("");
  const [newCategory, setNewCategory] = useState("");
  const [pickedSub, setPickedSub] = useState("");
  const [newSub, setNewSub] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [result, setResult] = useState<{ accepted: number; skipped: { filename: string; reason: string }[] } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const categoryNames = useMemo(() => tree?.categories.map((item) => item.name) || [], [tree]);
  useEffect(() => {
    if (!pickedCategory && categoryNames.length > 0) setPickedCategory(categoryNames[0]);
  }, [categoryNames, pickedCategory]);

  const effectiveCategory = pickedCategory === NEW_CATEGORY_SENTINEL ? newCategory.trim() : pickedCategory;
  const currentNode = tree?.categories.find((item) => item.name === effectiveCategory) || null;
  const needsSubcategory = Boolean(currentNode?.two_level);
  const existingSubs = currentNode?.subcategories || [];

  useEffect(() => {
    if (!needsSubcategory) {
      setPickedSub("");
      setNewSub("");
      return;
    }
    setPickedSub(existingSubs.length > 0 ? existingSubs[0] : NEW_CATEGORY_SENTINEL);
    setNewSub("");
  }, [effectiveCategory, needsSubcategory, existingSubs.join("|")]);

  const effectiveSub = needsSubcategory
    ? pickedSub === NEW_CATEGORY_SENTINEL ? newSub.trim() : pickedSub
    : "";

  const invalidFiles = files.filter((file) => {
    const lower = file.name.toLowerCase();
    return file.size === 0 || !SUPPORTED_EXTENSIONS.some((extension) => lower.endsWith(extension));
  });
  const validFiles = files.filter((file) => !invalidFiles.includes(file));
  const totalSize = validFiles.reduce((sum, file) => sum + file.size, 0);
  const canSubmit = validFiles.length > 0 && Boolean(effectiveCategory) && (!needsSubcategory || Boolean(effectiveSub));

  function addFiles(incoming: File[]) {
    setResult(null);
    setFiles((current) => {
      const map = new Map(current.map((file) => [`${file.name}-${file.size}-${file.lastModified}`, file]));
      incoming.forEach((file) => map.set(`${file.name}-${file.size}-${file.lastModified}`, file));
      return Array.from(map.values());
    });
  }

  async function submit() {
    if (!canSubmit) return;
    setSubmitting(true);
    setResult(null);
    try {
      const response = await api.adminUploadDocuments(validFiles, effectiveCategory, effectiveSub || undefined);
      setResult({ accepted: response.accepted.length, skipped: response.skipped });
      setFiles([]);
      if (fileInputRef.current) fileInputRef.current.value = "";
      await onUploaded();
    } catch (caught: any) {
      setResult({ accepted: 0, skipped: [{ filename: "本次上传", reason: caught?.message || String(caught) }] });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
      <div className="space-y-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="document-category" className="mb-1.5 block text-ui-sm font-medium">分类</label>
            <select id="document-category" value={pickedCategory} onChange={(event) => setPickedCategory(event.target.value)} disabled={submitting} className={selectClassName}>
              {categoryNames.length === 0 && <option value="">（暂无现有分类）</option>}
              {categoryNames.map((name) => <option key={name} value={name}>{name}</option>)}
              <option value={NEW_CATEGORY_SENTINEL}>＋ 新建分类…</option>
            </select>
          </div>
          {pickedCategory === NEW_CATEGORY_SENTINEL && (
            <div>
              <label htmlFor="document-new-category" className="mb-1.5 block text-ui-sm font-medium">新分类名称</label>
              <Input id="document-new-category" value={newCategory} onChange={(event) => setNewCategory(event.target.value)} placeholder="例如：行业规范" disabled={submitting} autoFocus />
            </div>
          )}
          {needsSubcategory && (
            <div>
              <label htmlFor="document-subcategory" className="mb-1.5 block text-ui-sm font-medium">{effectiveCategory === "客户标准" ? "客户" : "公司"}</label>
              <select id="document-subcategory" value={pickedSub} onChange={(event) => setPickedSub(event.target.value)} disabled={submitting} className={selectClassName}>
                {existingSubs.length === 0 && <option value={NEW_CATEGORY_SENTINEL}>（暂无；请新建）</option>}
                {existingSubs.map((name) => <option key={name} value={name}>{name}</option>)}
                {existingSubs.length > 0 && <option value={NEW_CATEGORY_SENTINEL}>＋ 新建…</option>}
              </select>
            </div>
          )}
          {needsSubcategory && pickedSub === NEW_CATEGORY_SENTINEL && (
            <div>
              <label htmlFor="document-new-subcategory" className="mb-1.5 block text-ui-sm font-medium">新{effectiveCategory === "客户标准" ? "客户" : "公司"}名称</label>
              <Input id="document-new-subcategory" value={newSub} onChange={(event) => setNewSub(event.target.value)} placeholder="输入名称" disabled={submitting} autoFocus />
            </div>
          )}
        </div>

        <div
          className={cn(
            "rounded-ui-xl border-2 border-dashed p-8 text-center transition-colors",
            dragging ? "border-primary bg-primary/5" : "border-border bg-surface-muted/30",
          )}
          onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={(event) => { if (event.currentTarget === event.target) setDragging(false); }}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            addFiles(Array.from(event.dataTransfer.files));
          }}
        >
          <Upload className="mx-auto size-8 text-primary" />
          <p className="mt-3 text-ui-sm font-medium">拖放文件到这里，或点击选择</p>
          <p className="mt-1 text-ui-xs text-muted-foreground">支持 PDF、DOCX、XLSX、PPTX 和 Markdown，可多选。</p>
          <Button type="button" variant="outline" size="sm" className="mt-4" onClick={() => fileInputRef.current?.click()} disabled={submitting}>
            选择文件
          </Button>
          <input
            ref={fileInputRef}
            id="corpus-upload-input"
            aria-label="文件"
            type="file"
            multiple
            accept={SUPPORTED_EXTENSIONS.join(",")}
            className="sr-only"
            disabled={submitting}
            onChange={(event) => addFiles(Array.from(event.target.files || []))}
          />
        </div>

        {files.length > 0 && (
          <section aria-labelledby="upload-queue-title">
            <div className="flex items-center justify-between">
              <h3 id="upload-queue-title" className="text-ui-sm font-semibold">待上传文件</h3>
              <span className="text-ui-xs text-muted-foreground">{validFiles.length} 个有效文件 · {formatBytes(totalSize)}</span>
            </div>
            <ul className="mt-2 divide-y divide-border rounded-ui-lg border border-border">
              {files.map((file) => {
                const invalid = invalidFiles.includes(file);
                return (
                  <li key={`${file.name}-${file.size}-${file.lastModified}`} className="flex items-center gap-3 px-3 py-2.5">
                    <FileText className={cn("size-4 shrink-0", invalid ? "text-destructive" : "text-muted-foreground")} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-ui-sm font-medium" title={file.name}>{file.name}</p>
                      <p className={cn("text-ui-xs", invalid ? "text-destructive" : "text-muted-foreground")}>
                        {file.size === 0 ? "空文件，不能上传" : invalid ? "不支持的文件格式" : formatBytes(file.size)}
                      </p>
                    </div>
                    <button type="button" aria-label={`移除 ${file.name}`} onClick={() => setFiles((current) => current.filter((item) => item !== file))} className="rounded-ui-sm p-2 text-muted-foreground hover:bg-secondary hover:text-foreground">
                      <X className="size-4" />
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        )}

        <details className="rounded-ui-lg border border-border bg-surface-muted/30 px-4 py-3">
          <summary className="flex cursor-pointer list-none items-center justify-between text-ui-sm font-medium">
            解析方式说明
            <ChevronDown className="size-4 text-muted-foreground" />
          </summary>
          <p className="mt-2 text-ui-xs leading-relaxed text-muted-foreground">
            PDF 使用 MinerU，DOCX 使用 Docling；教学视频分类中的 Markdown 按说话人和时间戳处理，其它 Markdown 按标题切分。
          </p>
        </details>

        {result && result.accepted > 0 && (
          <Alert variant="success"><AlertTitle>资料已加入处理队列</AlertTitle><AlertDescription>已受理 {result.accepted} 个文件，可关闭面板并在资料列表查看状态。</AlertDescription></Alert>
        )}
        {result && result.skipped.length > 0 && (
          <Alert variant="destructive" role="alert">
            <AlertTitle>{result.accepted > 0 ? "部分文件未受理" : "上传失败"}</AlertTitle>
            <AlertDescription><ul className="list-disc space-y-1 pl-5">{result.skipped.map((item, index) => <li key={`${item.filename}-${index}`}>{item.filename}：{item.reason}</li>)}</ul></AlertDescription>
          </Alert>
        )}
      </div>
      <div className="sticky bottom-0 mt-6 flex items-center justify-between gap-4 border-t border-border bg-card py-4">
        <p className="text-ui-xs text-muted-foreground">{canSubmit ? `${validFiles.length} 个文件已准备好` : "请完成分类并添加有效文件"}</p>
        <Button onClick={() => void submit()} disabled={submitting || !canSubmit}>
          {submitting ? "正在上传…" : `上传 ${validFiles.length} 个文件`}
        </Button>
      </div>
    </div>
  );
}

function JobStatusCell({ job }: { job: IndexJob }) {
  const active = ACTIVE_STATUSES.has(job.status);
  const elapsed = useElapsed(active ? job.started_at ?? job.created_at : null);
  const meta = STATUS_META[job.status];
  const activityLabel = job.status === "done" ? "处理完成" : meta?.label ?? job.status;
  return (
    <div>
      <Badge variant={meta?.variant ?? "secondary"}>
        {activityLabel}{elapsed ? ` · ${elapsed}` : ""}
      </Badge>
      {job.error && <p className="mt-1 max-w-sm text-ui-xs text-destructive">{job.error.length > 160 ? `${job.error.slice(0, 160)}…` : job.error}</p>}
      {!job.source_exists && (job.status === "failed" || job.status === "done") && (
        <p className="mt-1 text-ui-xs text-muted-foreground">源文件已删除，无法重试</p>
      )}
    </div>
  );
}

function JobsActivity({
  jobs,
  loading,
  onChanged,
}: {
  jobs: IndexJob[];
  loading: boolean;
  onChanged: () => Promise<void> | void;
}) {
  const activeCount = jobs.filter((job) => ACTIVE_STATUSES.has(job.status)).length;
  const [deleteTarget, setDeleteTarget] = useState<IndexJob | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function retry(job: IndexJob) {
    await api.adminRetryIndexJob(job.id);
    await onChanged();
  }

  async function deleteRecord() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.adminDeleteIndexJob(deleteTarget.id);
      setDeleteTarget(null);
      await onChanged();
    } finally {
      setDeleting(false);
    }
  }

  return (
    <>
      <details className="group rounded-ui-xl border border-border bg-card shadow-surface">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-4 py-4">
          <div className="flex items-center gap-3">
            <div className="flex size-9 items-center justify-center rounded-ui-md bg-secondary text-muted-foreground"><Activity className="size-4" /></div>
            <div>
              <h2 className="text-ui-sm font-semibold text-foreground">旧目录索引活动</h2>
              <p className="mt-0.5 text-ui-xs text-muted-foreground">最近 {jobs.length} 条处理记录{activeCount ? `，${activeCount} 条进行中` : ""}</p>
            </div>
          </div>
          <ChevronDown className="size-4 text-muted-foreground transition-transform group-open:rotate-180" />
        </summary>
        <div className="border-t border-border">
          {loading ? <LoadingState className="min-h-32" label="正在加载索引活动…" /> : jobs.length === 0 ? (
            <EmptyState title="暂无旧索引活动" description="兼容期旧任务记录会显示在这里。" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[60rem] text-ui-sm">
                <thead className="border-b border-border bg-surface-muted text-muted-foreground">
                  <tr><th className="px-4 py-3 text-left font-medium">文件</th><th className="px-4 py-3 text-left font-medium">状态</th><th className="px-4 py-3 text-left font-medium">上传者</th><th className="px-4 py-3 text-left font-medium">时间</th><th className="px-4 py-3 text-right font-medium">操作</th></tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {jobs.map((job) => (
                    <tr key={job.id} className="align-top">
                      <td className="px-4 py-3"><p className="max-w-xs truncate font-medium" title={job.filename}>{job.filename}</p><p className="mt-1 text-ui-xs text-muted-foreground">{job.category} · {formatBytes(job.file_size)}</p></td>
                      <td className="px-4 py-3"><JobStatusCell job={job} /></td>
                      <td className="px-4 py-3 text-muted-foreground">{job.real_name || "—"}</td>
                      <td className="px-4 py-3 text-ui-xs text-muted-foreground">{formatAdminDate(job.created_at)}</td>
                      <td className="px-4 py-3"><div className="flex justify-end gap-2">{job.source_exists && (job.status === "failed" || job.status === "done") && <Button size="sm" variant="outline" onClick={() => void retry(job)}>重试</Button>}{(job.status === "failed" || job.status === "done") && <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive" onClick={() => setDeleteTarget(job)}>删除记录</Button>}</div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </details>

      <Dialog open={deleteTarget != null} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除索引活动记录</DialogTitle>
            <DialogDescription>仅删除“{deleteTarget?.filename}”的任务记录，不影响源文件和已经建立的索引。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)} disabled={deleting}>取消</Button>
            <Button variant="destructive" onClick={() => void deleteRecord()} disabled={deleting}>{deleting ? "正在删除…" : "删除记录"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
