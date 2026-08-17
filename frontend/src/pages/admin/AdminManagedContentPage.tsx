import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, ArchiveRestore, ArrowDown, ArrowUp, ArrowUpDown, Captions, Check, CheckCircle2, ChevronDown, ChevronRight, Download, ExternalLink, Eye, FileCode2, FileSpreadsheet, FileText, FileType2, FileUp, Film, Folder, FolderInput, FolderPlus, Info, ListChecks, ListOrdered, Pencil, Presentation, RefreshCw, RotateCcw, Rocket, Search, Send, SlidersHorizontal, Trash2, Upload, X, XCircle } from "lucide-react";
import { adminContentApi } from "../../api/admin/content";
import { Badge } from "../../components/ui/badge";
import { CategoryTreePicker } from "../../components/admin/CategoryTreePicker";
import { Button, buttonVariants } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { Input } from "../../components/ui/input";
import { IconButton } from "../../components/ui/icon-button";
import { LoadingState } from "../../components/ui/loading-state";
import { Select } from "../../components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../../components/ui/sheet";
import { toast } from "../../components/ui/toast";
import { useAuth } from "../../context/AuthContext";
import { usePdfPreview } from "../../hooks/usePdfPreview";
import { useVideoPlayer } from "../../hooks/useVideoPlayer";
import type { BulkManagedContentResult, ContentPermission, FolderRequest, ManagedCategory, ManagedContentItem, ManagedUploadTask, ManagedUploadTaskEntry } from "../../types";
import type { ManagedUploadProgress } from "../../api/client";
import { formatAdminDate } from "../../lib/admin-formatters";
import { AdminDocumentsPage } from "./AdminDocumentsPage";
import { compareManagedCategories } from "../../lib/category-tree";
import {
  collectDroppedUpload,
  folderSelectionFromFiles,
  type FolderUploadEntry,
  type FolderUploadSelection,
} from "../../lib/folder-upload";

const PAGE_SIZE = 25;
const PAGE_SIZE_OPTIONS = [25, 50, 100];
const BULK_LIMIT = 20;
const BULK_DOWNLOAD_TOAST_ID = "managed-content-bulk-download";
const ACTIVE_RECLASSIFICATION_STATUSES = new Set(["pending", "applying", "committing", "rolling_back"]);
type SortKey = "docType" | "folderOrder" | "title" | "updatedAt" | "status" | "source";
type SortDirection = "asc" | "desc";
type ManagedContentView = "library" | "trash" | "uploads" | "index";
type MoveOperation = "move" | "reclassify" | "archive";
const ROOT_FOLDER_VALUE = "__root__";

function normalizeFolderName(value: string) {
  return value.normalize("NFKC").trim().toLocaleLowerCase("zh-CN");
}

function moveOperation(item: ManagedContentItem): MoveOperation {
  if (item.content_kind === "media_transcript") return "archive";
  return item.has_published_head ? "reclassify" : "move";
}

function formatManagedUpdatedAt(timestamp: number | null | undefined) {
  if (!timestamp) return "—";
  const date = new Date(timestamp * 1000);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function formatUploadSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatMediaDuration(milliseconds: number | null | undefined) {
  if (!milliseconds) return null;
  const totalSeconds = Math.floor(milliseconds / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
}

type ActiveUploadState = {
  batchId: string | null;
  uploadMode: "files" | "folder";
  targetPath: string;
  totalFiles: number;
  totalBytes: number;
  loadedBytes: number;
  phase: "uploading" | "processing" | "completed" | "failed";
  message?: string;
};

const uploadTaskStatusLabel: Record<string, string> = {
  processing: "处理中",
  completed: "已完成",
  partial_success: "部分成功",
  failed: "失败",
};

function uploadTaskStatusVariant(status: string) {
  if (status === "completed") return "success" as const;
  if (status === "failed") return "destructive" as const;
  if (status === "processing") return "secondary" as const;
  return "warning" as const;
}

function uploadTaskEntryStatus(entry: ManagedUploadTaskEntry) {
  return entry.status === "accepted" ? "已接收" : "已跳过";
}

function UploadTaskResult({ task }: { task: ManagedUploadTask }) {
  const total = Math.max(0, task.total_files);
  const accepted = Math.min(total, Math.max(0, task.accepted_files));
  const skipped = Math.min(total - accepted, Math.max(0, task.skipped_files));
  const unresolved = Math.max(0, total - accepted - skipped);
  const processed = accepted + skipped;
  const processedPercent = total > 0 ? Math.round((processed / total) * 100) : 0;
  const acceptedPercent = total > 0 ? (accepted / total) * 100 : 0;
  const skippedPercent = total > 0 ? (skipped / total) * 100 : 0;
  const unresolvedPercent = total > 0 ? (unresolved / total) * 100 : 0;

  const summary = task.status === "processing"
    ? `已处理 ${processed} / ${total} 个`
    : task.status === "completed"
      ? `已接收 ${accepted} / ${total} 个`
      : [accepted > 0 ? `已接收 ${accepted} 个` : null, skipped > 0 ? `跳过 ${skipped} 个` : null, unresolved > 0 ? `未完成 ${unresolved} 个` : null]
        .filter(Boolean)
        .join(" · ") || "没有文件被接收";

  return <div className="min-w-0">
    <div className="flex items-center justify-between gap-2 text-ui-xs">
      <span className="text-muted-foreground">{summary}</span>
      {task.status === "processing" && <span className="shrink-0 tabular-nums text-muted-foreground">{processedPercent}%</span>}
    </div>
    <div className="mt-1.5 flex h-1.5 overflow-hidden rounded-full bg-surface-muted" role="img" aria-label={`处理结果：${summary}`}>
      {acceptedPercent > 0 && <span className="h-full bg-success" style={{ width: `${acceptedPercent}%` }} />}
      {skippedPercent > 0 && <span className="h-full bg-warning" style={{ width: `${skippedPercent}%` }} />}
      {task.status === "failed" && unresolvedPercent > 0 && <span className="h-full bg-destructive" style={{ width: `${unresolvedPercent}%` }} />}
      {task.status === "processing" && processed === 0 && <span className="h-full w-1/4 animate-pulse bg-primary" />}
    </div>
  </div>;
}

function UploadTasksPanel({
  activeUpload,
  canRetry,
  onRetry,
}: {
  activeUpload: ActiveUploadState | null;
  canRetry: (task: ManagedUploadTask) => boolean;
  onRetry: (task: ManagedUploadTask) => void;
}) {
  const [tasks, setTasks] = useState<ManagedUploadTask[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "active" | "completed" | "partial_success" | "failed">("all");
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<ManagedUploadTask | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const pageSize = 10;

  useEffect(() => { setPage(0); }, [query, statusFilter]);

  const loadTasks = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const result = await adminContentApi.uploadTasks({
        status: statusFilter === "all" ? undefined : statusFilter === "active" ? "processing" : statusFilter,
        query: query || undefined,
        limit: pageSize,
        offset: page * pageSize,
      });
      setTasks(result.tasks); setTotal(result.total); setCounts(result.status_counts);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "上传任务加载失败");
    } finally { setLoading(false); }
  }, [page, query, statusFilter]);

  useEffect(() => { void loadTasks(); }, [loadTasks]);
  useEffect(() => {
    if (!activeUpload || activeUpload.phase === "uploading" || activeUpload.phase === "processing") return;
    void loadTasks();
  }, [activeUpload, loadTasks]);

  const openDetail = async (task: ManagedUploadTask) => {
    setDetail(task); setDetailLoading(true);
    try { setDetail(await adminContentApi.uploadTask(task.batch_id)); }
    catch (detailError) { setError(detailError instanceof Error ? detailError.message : "上传任务详情加载失败"); }
    finally { setDetailLoading(false); }
  };
  const visibleTasks = tasks;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const hasFilters = Boolean(query) || statusFilter !== "all";
  const applySearch = () => {
    const nextQuery = queryInput.trim();
    setPage(0);
    setQuery(nextQuery);
  };
  const clearFilters = () => {
    setQueryInput("");
    setQuery("");
    setStatusFilter("all");
    setPage(0);
  };
  const progressPercent = activeUpload && activeUpload.totalBytes > 0
    ? Math.min(100, Math.round((activeUpload.loadedBytes / activeUpload.totalBytes) * 100))
    : activeUpload?.phase === "completed" ? 100 : 0;
  const activeLabel = activeUpload?.phase === "uploading" ? "上传中" : activeUpload?.phase === "processing" ? "服务端处理中…" : activeUpload?.phase === "completed" ? "已完成" : "上传失败";
  const activeStatusIcon = activeUpload?.phase === "failed" ? <XCircle className="size-4 text-destructive" /> : activeUpload?.phase === "completed" ? <CheckCircle2 className="size-4 text-success" /> : <Upload className="size-4 animate-pulse text-primary" />;

  return <section className="space-y-4" aria-labelledby="upload-tasks-title">
    <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div><h2 id="upload-tasks-title" className="text-ui-xl font-semibold tracking-tight">上传任务</h2><p className="mt-1 text-ui-sm text-muted-foreground">查看文件和文件夹上传进度、结果与失败明细。</p></div>
      <Button size="sm" variant="outline" onClick={() => void loadTasks()} disabled={loading}><RefreshCw className={loading ? "size-4 animate-spin" : "size-4"} />刷新任务</Button>
    </header>
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" aria-label="上传任务状态概览">
      {[{ key: "active", label: "进行中", value: (counts.processing || 0) + (activeUpload && ["uploading", "processing"].includes(activeUpload.phase) ? 1 : 0), icon: <Upload className="size-4" /> }, { key: "completed", label: "已完成", value: counts.completed || 0, icon: <CheckCircle2 className="size-4" /> }, { key: "partial_success", label: "部分成功", value: counts.partial_success || 0, icon: <AlertTriangle className="size-4" /> }, { key: "failed", label: "失败", value: counts.failed || 0, icon: <XCircle className="size-4" /> }].map((summary) => <button type="button" key={summary.key} className={`rounded-ui-lg border bg-background p-3 text-left transition-colors hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${statusFilter === summary.key ? "border-primary ring-1 ring-primary/30" : "border-border"}`} aria-pressed={statusFilter === summary.key} onClick={() => setStatusFilter((current) => current === summary.key ? "all" : summary.key as typeof statusFilter)}><span className="flex items-center justify-between text-ui-xs text-muted-foreground"><span>{summary.label}</span>{summary.icon}</span><span className="mt-2 block text-ui-xl font-semibold tabular-nums">{summary.value}</span></button>)}
    </div>
    {activeUpload && <div className="rounded-ui-lg border border-primary/40 bg-primary/5 px-4 py-3" role="status" aria-live="polite"><div className="flex items-start gap-3">{activeStatusIcon}<div className="min-w-0 flex-1"><div className="flex flex-wrap items-center justify-between gap-2"><p className="font-medium">{activeLabel}</p><span className="text-ui-xs tabular-nums text-muted-foreground">{progressPercent}%</span></div><p className="mt-1 break-words text-ui-xs text-muted-foreground">{activeUpload.uploadMode === "folder" ? "文件夹" : "文件"} · {activeUpload.totalFiles} 个 · {activeUpload.targetPath}</p><div className="mt-2 h-2 overflow-hidden rounded-full bg-primary/15"><div className="h-full rounded-full bg-primary transition-[width] duration-normal" style={{ width: `${progressPercent}%` }} /></div>{activeUpload.message && <p className="mt-2 break-words text-ui-xs text-destructive">{activeUpload.message}</p>}</div></div></div>}
    <Card className="overflow-hidden shadow-surface">
      <div className="grid gap-3 border-b border-border px-4 py-4 sm:px-5 lg:grid-cols-[minmax(16rem,1fr)_auto] lg:items-end">
        <form className="grid min-w-0 gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end" role="search" onSubmit={(event) => { event.preventDefault(); applySearch(); }}>
          <label className="min-w-0 space-y-1 text-ui-xs text-muted-foreground"><span>搜索任务</span><span className="relative block"><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2" /><Input type="search" aria-label="搜索上传任务" className="pl-9" value={queryInput} onChange={(event) => setQueryInput(event.target.value)} placeholder="搜索目标目录或文件名…" /></span></label>
          <Button type="submit" className="h-control-md" disabled={loading || queryInput.trim() === query}><Search className="size-4" />搜索</Button>
        </form>
        <div className="flex flex-wrap items-center gap-2 lg:justify-end"><Button size="sm" variant="outline" onClick={clearFilters} disabled={!queryInput && !hasFilters}>清除筛选</Button><span className="text-ui-xs text-muted-foreground" role="status">共 {total} 个任务</span></div>
      </div>
      {error && <ErrorState title="上传任务加载失败" description={error} action={<Button size="sm" variant="outline" onClick={() => void loadTasks()}>重新加载</Button>} />}
      {loading ? <LoadingState className="min-h-48 border-0" label="正在加载上传任务…" /> : statusFilter === "active" && !activeUpload && visibleTasks.length === 0 ? <EmptyState className="rounded-none border-0" title="暂无进行中的上传" description="开始上传文件或文件夹后，进度会显示在这里。" action={<Button size="sm" variant="outline" onClick={clearFilters}>查看全部任务</Button>} /> : visibleTasks.length === 0 ? <EmptyState className="rounded-none border-0" title={hasFilters ? "没有符合条件的上传任务" : "暂无上传任务"} description={hasFilters ? "请调整搜索关键词或状态筛选。" : "上传文件或文件夹后，任务记录会显示在这里。"} action={hasFilters ? <Button size="sm" variant="outline" onClick={clearFilters}>清除筛选</Button> : undefined} /> : <>
        <div className="hidden grid-cols-[minmax(10rem,1.7fr)_5.5rem_minmax(11rem,1fr)_8rem_6rem_9.75rem] gap-x-4 border-b border-border bg-surface-muted/40 px-5 py-2.5 text-ui-xs font-medium text-muted-foreground xl:grid" data-testid="upload-task-header"><span>任务</span><span>文件</span><span>处理结果</span><span>创建时间</span><span>状态</span><span className="text-right">操作</span></div>
        <ul className="divide-y divide-border">{visibleTasks.map((task) => <li key={task.batch_id} className="flex flex-col gap-3 px-4 py-4 sm:px-5 xl:grid xl:grid-cols-[minmax(10rem,1.7fr)_5.5rem_minmax(11rem,1fr)_8rem_6rem_9.75rem] xl:items-center xl:gap-x-4" data-testid="upload-task-row"><div className="min-w-0"><p className="break-all font-medium">{task.upload_mode === "folder" ? "文件夹上传" : "文件上传"}</p><p className="mt-1 break-all text-ui-xs text-muted-foreground">{task.target_path}</p></div><div><span className="mb-1 block text-ui-xs font-medium text-muted-foreground xl:hidden">文件</span><span className="text-ui-xs text-muted-foreground">{task.upload_mode === "folder" ? "文件夹" : "文件"} · {task.total_files} 个</span></div><div><span className="mb-1 block text-ui-xs font-medium text-muted-foreground xl:hidden">处理结果</span><UploadTaskResult task={task} /></div><div><span className="mb-1 block text-ui-xs font-medium text-muted-foreground xl:hidden">创建时间</span><span className="text-ui-xs text-muted-foreground">{formatManagedUpdatedAt(task.created_at)}</span></div><div><span className="mb-1 block text-ui-xs font-medium text-muted-foreground xl:hidden">状态</span><Badge variant={uploadTaskStatusVariant(task.status)}>{uploadTaskStatusLabel[task.status]}</Badge></div><div className="flex gap-2 xl:justify-end"><Button size="sm" variant="outline" onClick={() => void openDetail(task)}><ListChecks className="size-4" />详情</Button>{task.status === "failed" && <Button size="sm" variant="outline" disabled={!canRetry(task)} onClick={() => onRetry(task)}><RotateCcw className="size-4" />重试</Button>}</div></li>)}</ul>
      </>}
      <div className="flex items-center justify-between border-t border-border px-4 py-3 sm:px-5"><p className="text-ui-xs text-muted-foreground">第 {page + 1} / {pageCount} 页</p><div className="flex gap-2"><Button size="sm" variant="outline" disabled={page === 0 || loading} onClick={() => setPage((value) => value - 1)}>上一页</Button><Button size="sm" variant="outline" disabled={page + 1 >= pageCount || loading} onClick={() => setPage((value) => value + 1)}>下一页</Button></div></div>
    </Card>
    <Sheet open={Boolean(detail)} onOpenChange={(open) => { if (!open) setDetail(null); }}><SheetContent className="max-w-xl overflow-y-auto"><SheetHeader><SheetTitle>上传任务详情</SheetTitle><SheetDescription>{detail?.target_path || "查看任务明细"}</SheetDescription></SheetHeader>{detailLoading ? <LoadingState className="mt-6 border-0" label="正在加载任务详情…" /> : detail && <div className="mt-6 space-y-5"><dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-2 text-ui-sm"><dt className="text-muted-foreground">状态</dt><dd><Badge variant={uploadTaskStatusVariant(detail.status)}>{uploadTaskStatusLabel[detail.status]}</Badge></dd><dt className="text-muted-foreground">目标目录</dt><dd className="break-words">{detail.target_path}</dd><dt className="text-muted-foreground">创建人</dt><dd>{detail.created_by_name}</dd><dt className="text-muted-foreground">创建时间</dt><dd>{formatManagedUpdatedAt(detail.created_at)}</dd><dt className="text-muted-foreground">文件统计</dt><dd>{detail.accepted_files} 个已接收，{detail.skipped_files} 个已跳过，共 {detail.total_files} 个</dd>{detail.error_summary && <><dt className="text-muted-foreground">失败原因</dt><dd className="break-words text-destructive">{detail.error_summary}</dd></>}</dl><div><h3 className="text-ui-sm font-semibold">文件明细</h3><ul className="mt-2 divide-y divide-border rounded-ui-md border border-border">{(detail.entries || []).map((entry) => <li key={entry.sequence} className="flex items-start justify-between gap-3 px-3 py-2 text-ui-sm"><span className="min-w-0"><span className="block break-all">{entry.relative_path || entry.filename}</span>{entry.reason && <span className="mt-0.5 block break-words text-ui-xs text-muted-foreground">{entry.reason}</span>}</span><span className={`shrink-0 text-ui-xs ${entry.status === "accepted" ? "text-success" : "text-warning"}`}>{uploadTaskEntryStatus(entry)}</span></li>)}</ul></div>{detail.status === "failed" && <Button variant="outline" disabled={!canRetry(detail)} onClick={() => { onRetry(detail); setDetail(null); }}><RotateCcw className="size-4" />重试此任务</Button>}</div>}</SheetContent></Sheet>
  </section>;
}

const statusLabel: Record<string, string> = {
  draft: "待提交", awaiting_review: "待确认", approved: "已确认", rejected: "已退回",
  publishing: "发布中", published: "已发布", publication_failed: "发布失败", superseded: "历史版本",
};
const sourceLabel: Record<string, string> = {
  web: "网页上传", server: "后台导入", legacy: "历史迁移", transcription: "视频转写",
};

const documentTypeOptions = [
  ["pdf", "PDF"],
  ["docx", "Word"],
  ["xlsx", "Excel"],
  ["pptx", "PPT"],
  ["markdown", "Markdown"],
  ["transcript", "视频转录稿"],
  ["other", "其他"],
] as const;

function ManagedItemType({ docType, folder = false }: { docType?: string; folder?: boolean }) {
  if (folder) return <div className="flex w-20 flex-col items-center gap-1 text-center text-ui-xs font-medium text-muted-foreground"><Folder className="size-6 text-primary" aria-hidden="true" /><span>文件夹</span></div>;
  const definition = ({
    pdf: ["PDF", FileText, "text-destructive"],
    docx: ["Word", FileType2, "text-primary"],
    xlsx: ["Excel", FileSpreadsheet, "text-success"],
    pptx: ["PPT", Presentation, "text-warning"],
    markdown: ["Markdown", FileCode2, "text-foreground"],
    transcript: ["视频转录稿", Captions, "text-primary"],
  } as const)[docType || ""] || (["其他", FileText, "text-muted-foreground"] as const);
  const [label, Icon, color] = definition;
  return <div className="flex w-20 flex-col items-center gap-1 text-center text-ui-xs font-medium" title={label}><Icon className={`size-6 ${color}`} aria-hidden="true" /><span className="max-w-full break-words leading-tight">{label}</span></div>;
}

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

function ManagedItemIdentity({ item }: { item: ManagedContentItem }) {
  const isMediaTranscript = item.content_kind === "media_transcript";
  const mediaDetails = isMediaTranscript
    ? [formatMediaDuration(item.media_duration_ms), item.media_file_size != null ? formatUploadSize(item.media_file_size) : null].filter(Boolean)
    : [];
  return <div className="min-w-0">
    <p className="break-words font-medium">{item.title}</p>
    <p className="mt-0.5 break-all text-ui-xs text-muted-foreground">{item.original_filename} · v{item.version_number}{mediaDetails.length ? ` · ${mediaDetails.join(" · ")}` : ""}</p>
    {isMediaTranscript && item.has_pending_revision && <div className="mt-2 flex flex-wrap gap-1.5"><Badge variant="warning">有新转录稿待处理</Badge></div>}
  </div>;
}

type BulkAction = "move" | "approve" | "reject" | "publish" | "download" | "archive";

type FilenameConflict = {
  item_id: string;
  version_id: string;
  title: string;
  original_filename: string;
};

function filenameForOldMode(originalFilename: string, incomingFilename: string) {
  const incomingDot = incomingFilename.lastIndexOf(".");
  const originalDot = originalFilename.lastIndexOf(".");
  if (incomingDot <= 0 || incomingDot === incomingFilename.length - 1) return originalFilename;
  const incomingSuffix = incomingFilename.slice(incomingDot).toLocaleLowerCase("en-US");
  const originalSuffix = originalDot > 0 ? originalFilename.slice(originalDot).toLocaleLowerCase("en-US") : "";
  return originalSuffix === incomingSuffix
    ? originalFilename
    : `${originalDot > 0 ? originalFilename.slice(0, originalDot) : originalFilename}${incomingSuffix}`;
}

function filenameConflictFrom(error: unknown): FilenameConflict | null {
  const candidate = error as { code?: unknown; body?: unknown } | null;
  if (!candidate || candidate.code !== "content_filename_conflict" || typeof candidate.body !== "string") return null;
  try {
    const conflict = JSON.parse(candidate.body)?.detail?.conflict;
    if (conflict?.item_id && conflict?.version_id && conflict?.title && conflict?.original_filename) {
      return conflict as FilenameConflict;
    }
  } catch {
    return null;
  }
  return null;
}

function triggerManagedDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}

function BatchActionsMenu({
  disabled,
  options,
}: {
  disabled: boolean;
  options: Array<{ key: BulkAction; label: string; icon: ReactNode; disabled?: boolean; disabledReason?: string; destructive?: boolean; onSelect: () => void }>;
}) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!triggerRef.current?.contains(target) && !menuRef.current?.contains(target)) setOpen(false);
    };
    const closeEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setOpen(false); triggerRef.current?.focus(); }
    };
    document.addEventListener("mousedown", closeOutside);
    document.addEventListener("keydown", closeEscape);
    return () => {
      document.removeEventListener("mousedown", closeOutside);
      document.removeEventListener("keydown", closeEscape);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !triggerRef.current || !menuRef.current) return;
    const triggerRect = triggerRef.current.getBoundingClientRect();
    const menuRect = menuRef.current.getBoundingClientRect();
    const minimumOffset = 12;
    const preferredTop = triggerRect.bottom + 6;
    const maximumTop = window.innerHeight - menuRect.height - minimumOffset;
    const top = Math.min(preferredTop, Math.max(minimumOffset, maximumTop));
    const left = Math.min(
      Math.max(minimumOffset, triggerRect.right - menuRect.width),
      Math.max(minimumOffset, window.innerWidth - menuRect.width - minimumOffset),
    );
    setPosition({ top, left });
  }, [open, options.length]);

  useEffect(() => {
    if (!open) return;
    const frame = window.requestAnimationFrame(() => {
      optionRefs.current.find((option) => option && !option.disabled)?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [open]);

  const handleMenuKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const enabledOptions = optionRefs.current.filter((option): option is HTMLButtonElement => Boolean(option && !option.disabled));
    if (!enabledOptions.length) return;
    const currentIndex = enabledOptions.indexOf(document.activeElement as HTMLButtonElement);
    const nextIndex = event.key === "Home" ? 0
      : event.key === "End" ? enabledOptions.length - 1
      : event.key === "ArrowDown" ? (currentIndex + 1 + enabledOptions.length) % enabledOptions.length
      : (currentIndex - 1 + enabledOptions.length) % enabledOptions.length;
    enabledOptions[nextIndex].focus();
  };

  const toggle = () => {
    if (!open && triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      const menuWidth = 176;
      const maximumLeft = Math.max(12, window.innerWidth - menuWidth - 12);
      setPosition({
        top: rect.bottom + 6,
        left: Math.min(Math.max(12, rect.right - menuWidth), maximumLeft),
      });
    }
    setOpen((current) => !current);
  };

  return <>
    <Button ref={triggerRef} size="sm" variant="outline" className="max-sm:h-control-md" disabled={disabled} aria-haspopup="menu" aria-expanded={open} onClick={toggle}>
      批量操作<ChevronDown className="size-4" />
    </Button>
    {open && createPortal(<div ref={menuRef} role="menu" aria-label="批量操作" className="fixed z-dropdown w-44 overflow-hidden rounded-ui-lg border border-border bg-popover p-1.5 text-popover-foreground shadow-overlay" style={position} onKeyDown={handleMenuKeyDown}>
      {options.map((option, index) => <div key={option.key}>
        {option.destructive && index > 0 && <div className="my-1 border-t border-border" role="separator" />}
        <button ref={(element) => { optionRefs.current[index] = element; }} type="button" role="menuitem" disabled={option.disabled} title={option.disabled ? option.disabledReason : undefined} className={`flex w-full items-center gap-2.5 rounded-ui-md px-2.5 py-2 text-left text-ui-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40 ${option.destructive ? "text-destructive hover:bg-destructive/10" : "hover:bg-surface-muted"}`} onClick={() => { setOpen(false); option.onSelect(); }}>
          {option.icon}{option.label}
        </button>
      </div>)}
    </div>, document.body)}
  </>;
}

function ManagedContentSearchFilters({
  queryInput,
  statusFilter,
  sourceFilter,
  kindFilter,
  disabled,
  onQueryInputChange,
  onStatusFilterChange,
  onSourceFilterChange,
  onKindFilterChange,
  onClear,
}: {
  queryInput: string;
  statusFilter: string;
  sourceFilter: string;
  kindFilter: string;
  disabled: boolean;
  onQueryInputChange: (value: string) => void;
  onStatusFilterChange: (value: string) => void;
  onSourceFilterChange: (value: string) => void;
  onKindFilterChange: (value: string) => void;
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const activeFilterCount = Number(Boolean(statusFilter)) + Number(Boolean(sourceFilter)) + Number(Boolean(kindFilter));
  const panelId = "managed-content-search-filters";

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      inputRef.current?.focus();
      setOpen(false);
    };
    document.addEventListener("mousedown", closeOutside);
    document.addEventListener("keydown", closeEscape);
    return () => {
      document.removeEventListener("mousedown", closeOutside);
      document.removeEventListener("keydown", closeEscape);
    };
  }, [open]);

  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  const filterButtonLabel = open ? "收起搜索筛选" : "展开搜索筛选";

  return <div ref={rootRef} className="relative min-w-0 w-full xl:w-72 xl:max-w-72 xl:justify-self-center min-[1400px]:w-96 min-[1400px]:max-w-96">
    <div className="relative">
      <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
      <Input
        ref={inputRef}
        className="h-control-sm pl-9 pr-11"
        value={queryInput}
        onChange={(event) => onQueryInputChange(event.target.value)}
        onFocus={() => setOpen(true)}
        aria-label="搜索资料"
        placeholder={disabled ? "选择目录后搜索资料" : "搜索名称或文件名…"}
        disabled={disabled}
      />
      <button
        type="button"
        className="absolute right-1 top-1/2 flex size-7 -translate-y-1/2 items-center justify-center rounded-ui-sm text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
        aria-label={filterButtonLabel}
        title={filterButtonLabel}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={panelId}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
      >
        <SlidersHorizontal className="size-4" aria-hidden="true" />
        {activeFilterCount > 0 && <span className="absolute right-0.5 top-0.5 size-1.5 rounded-full bg-primary" aria-hidden="true" />}
        {activeFilterCount > 0 && <span className="sr-only">，已启用 {activeFilterCount} 项筛选</span>}
      </button>
    </div>
    {open && <div id={panelId} role="dialog" aria-modal="false" aria-label="搜索筛选" className="absolute inset-x-0 top-full z-dropdown mt-2 rounded-ui-lg border border-border bg-popover p-3 text-popover-foreground shadow-overlay">
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="space-y-1 text-ui-xs text-muted-foreground"><span>类型</span><Select className="h-control-sm" value={kindFilter} onChange={(event) => onKindFilterChange(event.target.value)}><option value="">全部类型</option>{documentTypeOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></label>
        <label className="space-y-1 text-ui-xs text-muted-foreground"><span>状态</span><Select className="h-control-sm" value={statusFilter} onChange={(event) => onStatusFilterChange(event.target.value)}><option value="">全部状态</option>{Object.entries(statusLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></label>
        <label className="space-y-1 text-ui-xs text-muted-foreground"><span>来源</span><Select className="h-control-sm" value={sourceFilter} onChange={(event) => onSourceFilterChange(event.target.value)}><option value="">全部来源</option>{Object.entries(sourceLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></label>
      </div>
      <div className="mt-3 flex flex-col gap-2 border-t border-border pt-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-ui-xs text-muted-foreground" role="status">{activeFilterCount > 0 ? `已启用 ${activeFilterCount} 项筛选` : "未启用附加筛选"}</p>
        <Button size="sm" variant="outline" onClick={onClear} disabled={!queryInput && activeFilterCount === 0}>清除搜索与筛选</Button>
      </div>
    </div>}
  </div>;
}

export function AdminManagedContentPage() {
  const { state } = useAuth();
  const { open: openDocumentPreview, state: previewState } = usePdfPreview();
  const { open: openVideoPreview } = useVideoPlayer();
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
  const [pendingFolderUpload, setPendingFolderUpload] = useState<FolderUploadSelection | null>(null);
  const [pendingFolderUploadFolderId, setPendingFolderUploadFolderId] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [activeUpload, setActiveUpload] = useState<ActiveUploadState | null>(null);
  const [lastUploadAttempt, setLastUploadAttempt] = useState<{ batchId: string; files: Array<File | FolderUploadEntry>; categoryId: string; uploadMode: "files" | "folder" } | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [bulkAction, setBulkAction] = useState<BulkAction | null>(null);
  const [bulkFailures, setBulkFailures] = useState<Array<BulkManagedContentResult & { title: string }>>([]);
  const [bulkNote, setBulkNote] = useState("");
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [kindFilter, setKindFilter] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [detail, setDetail] = useState<ManagedContentItem | null>(null);
  const [reviewTarget, setReviewTarget] = useState<ManagedContentItem | null>(null);
  const [reviewDecision, setReviewDecision] = useState<"approve" | "reject">("approve");
  const [reviewNote, setReviewNote] = useState("");
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [publishTarget, setPublishTarget] = useState<ManagedContentItem | null>(null);
  const [publishError, setPublishError] = useState<string | null>(null);
  const [sort, setSort] = useState<{ key: SortKey; direction: SortDirection } | null>(null);
  const [deleteTargets, setDeleteTargets] = useState<ManagedContentItem[]>([]);
  const [deleteAcknowledged, setDeleteAcknowledged] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [view, setViewState] = useState<ManagedContentView>(() => {
    if (typeof window === "undefined") return "library";
    const requestedView = new URLSearchParams(window.location.search).get("view");
    return requestedView === "uploads" || requestedView === "index" ? requestedView : "library";
  });
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
  const [moveError, setMoveError] = useState<string | null>(null);
  const [bulkMoveFolderId, setBulkMoveFolderId] = useState("");
  const [renameTarget, setRenameTarget] = useState<ManagedContentItem | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [renameFilename, setRenameFilename] = useState("");
  const [renameConflict, setRenameConflict] = useState<FilenameConflict | null>(null);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [updateTarget, setUpdateTarget] = useState<ManagedContentItem | null>(null);
  const [updateFile, setUpdateFile] = useState<File | null>(null);
  const [updateFilenameMode, setUpdateFilenameMode] = useState<"old" | "new">("old");
  const [updateConflict, setUpdateConflict] = useState<FilenameConflict | null>(null);
  const [updateError, setUpdateError] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [folderScanning, setFolderScanning] = useState(false);
  const [listDropActive, setListDropActive] = useState(false);
  const [listDropPromptTop, setListDropPromptTop] = useState(96);
  const [draggedItem, setDraggedItem] = useState<ManagedContentItem | null>(null);
  const [folderRequests, setFolderRequests] = useState<FolderRequest[]>([]);
  const [requestFolderOpen, setRequestFolderOpen] = useState(false);
  const [requestFolderName, setRequestFolderName] = useState("");
  const [folderRenameTarget, setFolderRenameTarget] = useState<ManagedCategory | null>(null);
  const [folderRenameName, setFolderRenameName] = useState("");
  const [folderSortTarget, setFolderSortTarget] = useState<ManagedCategory | null>(null);
  const [folderSortValue, setFolderSortValue] = useState("");
  const [folderMoveTarget, setFolderMoveTarget] = useState<ManagedCategory | null>(null);
  const [folderMoveParentId, setFolderMoveParentId] = useState("");
  const [folderActionError, setFolderActionError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const updateFileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const listDragDepthRef = useRef(0);

  const setView = (nextView: ManagedContentView) => {
    setViewState(nextView);
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    if (nextView === "library") params.delete("view");
    else params.set("view", nextView);
    const queryString = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${queryString ? `?${queryString}` : ""}`);
  };
  useEffect(() => {
    if (view === "uploads" && !can("item.upload")) setView("library");
    if (view === "index" && !can("index.view")) setView("library");
  }, [view, state.status, permissions]);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(queryInput.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [queryInput]);
  useEffect(() => { setPage(0); setSelected([]); }, [query, currentFolderId, statusFilter, sourceFilter, kindFilter, pageSize]);

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true);
    else if (!currentFolderId) setLoading(true);
    setError(null);
    try {
      const [capabilities, categoryRows, listing] = await Promise.all([
        adminContentApi.capabilities(), adminContentApi.categories(), adminContentApi.items({
          query: query || undefined,
          category_id: currentFolderId || undefined,
          lifecycle_status: statusFilter || undefined,
          source_origin: sourceFilter || undefined,
          doc_type: kindFilter ? kindFilter as "pdf" | "docx" | "xlsx" | "pptx" | "markdown" | "transcript" | "other" : undefined,
          sort_by: sort?.key === "docType" ? "doc_type" : undefined,
          sort_direction: sort?.key === "docType" ? sort.direction : undefined,
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
      if (can("folder.review")) {
        setFolderRequests(await adminContentApi.folderRequests("pending"));
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "资料加载失败");
    } finally {
      setLoading(false); setRefreshing(false);
    }
  }, [currentFolderId, page, pageSize, query, sourceFilter, statusFilter, kindFilter, sort]);

  useEffect(() => { void load(); }, [load]);
  const hasActiveReclassification = items.some((item) =>
    ACTIVE_RECLASSIFICATION_STATUSES.has(item.reclassification_status || ""),
  );
  useEffect(() => {
    if (!hasActiveReclassification || view !== "library") return undefined;
    const timer = window.setInterval(() => { void load(); }, 2000);
    return () => window.clearInterval(timer);
  }, [hasActiveReclassification, load, view]);

  const loadTrash = useCallback(async () => {
    if (!can("trash.view")) return;
    setTrashLoading(true); setError(null);
    try {
      const listing = await adminContentApi.trash({ query: query || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE });
      setTrashItems(listing.items); setTrashTotal(listing.total);
    } catch (trashFailure) {
      setError(trashFailure instanceof Error ? trashFailure.message : "回收站加载失败");
    } finally { setTrashLoading(false); }
  }, [page, query]);

  useEffect(() => { if (view === "trash") void loadTrash(); }, [loadTrash, view]);

  const upload = async (
    targetFolderId = currentFolderId,
    uploadFiles: Array<File | FolderUploadEntry> = files,
    uploadMode: "files" | "folder" = "files",
  ) => {
    const totalBytes = uploadFiles.reduce((sum, entry) => sum + ("file" in entry ? entry.file.size : entry.size), 0);
    const targetPath = categories.find((category) => category.id === targetFolderId)?.full_path || "当前目录";
    setUploading(true);
    setActiveUpload({ batchId: null, uploadMode, targetPath, totalFiles: uploadFiles.length, totalBytes, loadedBytes: 0, phase: "uploading" });
    try {
      const onProgress = (progress: ManagedUploadProgress) => setActiveUpload((current) => current ? {
        ...current,
        phase: progress.phase,
        loadedBytes: progress.phase === "processing" ? current.totalBytes : progress.loaded,
      } : current);
      const result = uploadMode === "folder"
        ? await adminContentApi.upload(uploadFiles, targetFolderId, "folder", onProgress)
        : await adminContentApi.upload(uploadFiles, targetFolderId, "files", onProgress);
      const accepted = result.entries.filter((entry) => entry.status === "accepted").length;
      const skipped = result.entries.length - accepted;
      setActiveUpload((current) => current ? { ...current, batchId: result.batch_id, loadedBytes: current.totalBytes, phase: accepted ? "completed" : "failed", message: accepted ? undefined : "没有文件被接收，请查看任务详情" } : current);
      if (!accepted) setLastUploadAttempt({ batchId: result.batch_id, files: uploadFiles, categoryId: targetFolderId, uploadMode });
      else setLastUploadAttempt(null);
      toast.success(skipped ? `已接收 ${accepted} 个文件，跳过 ${skipped} 个` : `已接收 ${accepted} 个文件`);
      setFiles([]);
      if (fileInputRef.current) fileInputRef.current.value = "";
      if (folderInputRef.current) folderInputRef.current.value = "";
      await load(true);
      return true;
    } catch (uploadError) {
      const message = uploadError instanceof Error ? uploadError.message : "上传失败";
      setActiveUpload((current) => current ? { ...current, phase: "failed", message } : current);
      toast.error(message);
    }
    finally { setUploading(false); }
    return false;
  };

  const retryUploadTask = async (task: ManagedUploadTask) => {
    if (!lastUploadAttempt || lastUploadAttempt.batchId !== task.batch_id) {
      toast.error("原始文件已不在当前页面，请重新选择后上传");
      return;
    }
    await upload(lastUploadAttempt.categoryId, lastUploadAttempt.files, lastUploadAttempt.uploadMode);
  };

  const prepareFileDrop = (incoming: File[]) => {
    const supported = incoming.filter((file) => /\.(pdf|md|docx|xlsx|pptx)$/i.test(file.name));
    setListDropActive(false);
    if (!supported.length || !currentFolderId) {
      if (incoming.length) toast.error("没有可上传的支持格式，仅支持 PDF、Markdown、Word、Excel 和 PPT 文件");
      return;
    }
    setPendingUploadFiles(supported);
    setPendingUploadFolderId(currentFolderId);
    setListDropActive(false);
  };

  const confirmFileDropUpload = async () => {
    if (!pendingUploadFiles.length || !pendingUploadFolderId) return;
    const targetFolderId = pendingUploadFolderId;
    if (await upload(targetFolderId, pendingUploadFiles)) {
      setPendingUploadFiles([]);
      setPendingUploadFolderId("");
    }
  };

  const prepareFolderSelection = (selection: FolderUploadSelection) => {
    clearListDropState();
    setDragActive(false);
    if (!currentFolderId) {
      toast.error("请先选择资料目录");
      return;
    }
    if (!selection.fileCount) {
      const ignored = selection.ignoredEntries.length;
      toast.error(ignored
        ? `文件夹中的 ${ignored} 个文件均不是支持的资料格式`
        : "所选文件夹中没有可上传的文件");
      return;
    }
    setUploadDialogOpen(false);
    setFiles([]);
    if (fileInputRef.current) fileInputRef.current.value = "";
    setPendingFolderUpload(selection);
    setPendingFolderUploadFolderId(currentFolderId);
  };

  const inspectDroppedUpload = async (dataTransfer: DataTransfer, plainFileAction: "confirm" | "select" = "confirm") => {
    setFolderScanning(true);
    try {
      const dropped = await collectDroppedUpload(dataTransfer);
      if (dropped.mode === "folder") prepareFolderSelection(dropped.selection);
      else if (plainFileAction === "select") acceptFiles(dropped.files);
      else prepareFileDrop(dropped.files);
    } catch (scanError) {
      toast.error(scanError instanceof Error ? scanError.message : "读取文件夹失败，请重新选择");
    } finally {
      setFolderScanning(false);
    }
  };

  const selectFolder = (incoming: File[]) => {
    setFolderScanning(true);
    try {
      prepareFolderSelection(folderSelectionFromFiles(incoming));
    } catch (scanError) {
      toast.error(scanError instanceof Error ? scanError.message : "读取文件夹失败，请重新选择");
    } finally {
      setFolderScanning(false);
      if (folderInputRef.current) folderInputRef.current.value = "";
    }
  };

  const confirmFolderUpload = async () => {
    if (!pendingFolderUpload?.entries.length || !pendingFolderUploadFolderId) return;
    if (await upload(pendingFolderUploadFolderId, pendingFolderUpload.entries, "folder")) {
      setPendingFolderUpload(null);
      setPendingFolderUploadFolderId("");
    }
  };

  const currentFolder = categories.find((category) => category.id === currentFolderId) || null;
  const currentFolderDropLabel = currentFolder ? `${currentFolder.display_code} ${currentFolder.display_name}`.trim() : "当前目录";
  const listDropEnabled = enabled && can("item.upload") && Boolean(currentFolderId) && !uploading && !folderScanning;
  const clearListDropState = () => {
    listDragDepthRef.current = 0;
    setListDropActive(false);
  };
  const positionListDropPrompt = (target: HTMLDivElement) => {
    const bounds = target.getBoundingClientRect();
    const visibleTop = Math.max(bounds.top, 0);
    const visibleBottom = Math.min(bounds.bottom, window.innerHeight);
    if (visibleBottom <= visibleTop) return;
    const edgeInset = Math.min(80, bounds.height / 2);
    const visibleCenter = (visibleTop + visibleBottom) / 2 - bounds.top;
    const promptTop = Math.round(Math.min(Math.max(edgeInset, bounds.height - edgeInset), Math.max(edgeInset, visibleCenter)));
    setListDropPromptTop((current) => current === promptTop ? current : promptTop);
  };
  const handleListDragEnter = (event: DragEvent<HTMLDivElement>) => {
    if (!listDropEnabled || !event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    listDragDepthRef.current += 1;
    positionListDropPrompt(event.currentTarget);
    setListDropActive(true);
  };
  const handleListDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!listDropEnabled || !event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    positionListDropPrompt(event.currentTarget);
  };
  const handleListDragLeave = (event: DragEvent<HTMLDivElement>) => {
    if (!event.dataTransfer.types.includes("Files")) return;
    listDragDepthRef.current = Math.max(0, listDragDepthRef.current - 1);
    if (listDragDepthRef.current === 0) setListDropActive(false);
  };
  const handleListDrop = (event: DragEvent<HTMLDivElement>) => {
    const dataTransfer = event.dataTransfer;
    clearListDropState();
    if (!listDropEnabled || !dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    const hasDirectory = Array.from(dataTransfer.items || []).some((item) => (
      (item as DataTransferItem & { webkitGetAsEntry?: () => { isDirectory?: boolean } | null })
        .webkitGetAsEntry?.()?.isDirectory
    ));
    if (!hasDirectory) {
      prepareFileDrop(Array.from(dataTransfer.files || []));
      return;
    }
    void inspectDroppedUpload(dataTransfer);
  };
  const childFolders = useMemo(() => categories.filter((category) => (
    category.parent_id === (currentFolderId || null) && category.is_active
  )), [categories, currentFolderId]);
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
  const sortedChildFolders = useMemo(() => {
    if (sort?.key === "folderOrder") {
      return [...childFolders].sort((left, right) => {
        const leftUnset = left.sort_order <= 0;
        const rightUnset = right.sort_order <= 0;
        if (leftUnset !== rightUnset) return leftUnset ? 1 : -1;
        const orderComparison = left.sort_order - right.sort_order;
        if (orderComparison !== 0) return sort.direction === "asc" ? orderComparison : -orderComparison;
        return compareManagedCategories(left, right);
      });
    }
    if (sort?.key !== "title") return childFolders;
    return [...childFolders].sort((left, right) => {
      const comparison = `${left.display_code} ${left.display_name}`.localeCompare(
        `${right.display_code} ${right.display_name}`,
        "zh-CN",
        { numeric: true, sensitivity: "base" },
      );
      return sort.direction === "asc" ? comparison : -comparison;
    });
  }, [childFolders, sort]);
  const folderRenameConflict = useMemo(() => {
    if (!folderRenameTarget) return null;
    const key = normalizeFolderName(folderRenameName);
    if (!key) return null;
    return categories.find((category) => category.id !== folderRenameTarget.id
      && category.parent_id === folderRenameTarget.parent_id
      && normalizeFolderName(category.display_name) === key) || null;
  }, [categories, folderRenameName, folderRenameTarget]);
  const parsedFolderSortOrder = folderSortValue.trim() ? Number(folderSortValue) : 0;
  const folderSortValid = Number.isInteger(parsedFolderSortOrder)
    && parsedFolderSortOrder >= 0
    && parsedFolderSortOrder <= 999_999;
  const folderSortDuplicateCount = folderSortTarget && parsedFolderSortOrder > 0
    ? categories.filter((category) => category.id !== folderSortTarget.id
      && category.parent_id === folderSortTarget.parent_id
      && category.sort_order === parsedFolderSortOrder).length
    : 0;
  const folderMoveConstraints = useMemo(() => {
    const reasons: Record<string, string> = {};
    if (!folderMoveTarget) return { reasons, rootReason: "" };
    const descendants = new Set<string>();
    const visit = (parentId: string) => categories.filter((category) => category.parent_id === parentId).forEach((child) => {
      descendants.add(child.id);
      visit(child.id);
    });
    visit(folderMoveTarget.id);
    const subtreeHeight = Math.max(0, ...categories
      .filter((category) => descendants.has(category.id))
      .map((category) => category.level - folderMoveTarget.level));
    const destinationConflict = (parentId: string | null) => {
      const siblings = categories.filter((category) => category.id !== folderMoveTarget.id && category.parent_id === parentId);
      if (siblings.some((category) => normalizeFolderName(category.display_name) === normalizeFolderName(folderMoveTarget.display_name))) {
        return `目标目录下已存在同名文件夹“${folderMoveTarget.display_name}”`;
      }
      if (siblings.some((category) => category.display_code === folderMoveTarget.display_code)) {
        return `目标目录下已存在显示编号“${folderMoveTarget.display_code}”`;
      }
      return "";
    };
    categories.forEach((category) => {
      if (category.id === folderMoveTarget.id) reasons[category.id] = "不能移动到文件夹自身";
      else if (descendants.has(category.id)) reasons[category.id] = "不能移动到自身的子目录";
      else if (category.id === folderMoveTarget.parent_id) reasons[category.id] = "文件夹已经位于此目录";
      else if (category.level + 1 + subtreeHeight > 4) reasons[category.id] = "移动后目录层级将超过四级";
      else reasons[category.id] = destinationConflict(category.id);
    });
    const rootReason = folderMoveTarget.parent_id === null
      ? "文件夹已经位于根目录"
      : destinationConflict(null);
    return { reasons, rootReason };
  }, [categories, folderMoveTarget]);
  const sortedItems = useMemo(() => {
    if (!sort) return items;
    if (sort.key === "docType" || sort.key === "folderOrder") return items;
    return [...items].sort((left, right) => {
      let comparison: number;
      switch (sort.key) {
        case "updatedAt": comparison = (left.updated_at || 0) - (right.updated_at || 0); break;
        case "status": comparison = (statusLabel[left.lifecycle_status] || left.lifecycle_status).localeCompare(statusLabel[right.lifecycle_status] || right.lifecycle_status, "zh-CN", { numeric: true, sensitivity: "base" }); break;
        case "source": comparison = (sourceLabel[left.source_origin] || left.source_origin).localeCompare(sourceLabel[right.source_origin] || right.source_origin, "zh-CN", { numeric: true, sensitivity: "base" }); break;
        default: comparison = left.title.localeCompare(right.title, "zh-CN", { numeric: true, sensitivity: "base" });
      }
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
      const usedCodes = new Set(childFolders.map((folder) => folder.display_code));
      let siblingNumber = 1;
      while (usedCodes.has(String(siblingNumber).padStart(2, "0"))) siblingNumber += 1;
      const nextSortOrder = Math.max(0, ...childFolders.map((folder) => folder.sort_order)) + 10;
      await adminContentApi.createCategory({
        parent_id: currentFolder.id,
        display_code: String(siblingNumber).padStart(2, "0"),
        display_name: newFolderName.trim(),
        sort_order: nextSortOrder,
      });
      setNewFolderName(""); setNewFolderOpen(false); toast.success("文件夹已创建"); await load(true);
    } catch (folderError) { toast.error(folderError instanceof Error ? folderError.message : "创建文件夹失败"); }
    finally { setBusyAction(null); }
  };

  const openFolderRename = (folder: ManagedCategory) => {
    setFolderRenameTarget(folder);
    setFolderRenameName(folder.display_name);
    setFolderActionError(null);
  };

  const saveFolderRename = async () => {
    if (!folderRenameTarget || !folderRenameName.trim() || folderRenameConflict) return;
    setBusyAction(`folder:${folderRenameTarget.id}:rename`);
    setFolderActionError(null);
    try {
      await adminContentApi.renameCategory(folderRenameTarget.id, {
        display_name: folderRenameName.trim(),
        expected_version: folderRenameTarget.version,
      });
      toast.success(`已重命名为“${folderRenameName.trim()}”`);
      setFolderRenameTarget(null);
      await load(true);
    } catch (renameError) {
      setFolderActionError(renameError instanceof Error ? renameError.message : "重命名文件夹失败");
    } finally {
      setBusyAction(null);
    }
  };

  const openFolderSort = (folder: ManagedCategory) => {
    setFolderSortTarget(folder);
    setFolderSortValue(folder.sort_order > 0 ? String(folder.sort_order) : "");
    setFolderActionError(null);
  };

  const saveFolderSort = async () => {
    if (!folderSortTarget || !folderSortValid) return;
    setBusyAction(`folder:${folderSortTarget.id}:sort`);
    setFolderActionError(null);
    try {
      await adminContentApi.updateCategorySortOrder(folderSortTarget.id, {
        sort_order: parsedFolderSortOrder,
        expected_version: folderSortTarget.version,
      });
      toast.success(parsedFolderSortOrder > 0 ? `排序序号已设置为 ${parsedFolderSortOrder}` : "已清除排序序号");
      setFolderSortTarget(null);
      await load(true);
    } catch (sortError) {
      setFolderActionError(sortError instanceof Error ? sortError.message : "设置排序序号失败");
    } finally {
      setBusyAction(null);
    }
  };

  const openFolderMove = (folder: ManagedCategory) => {
    setFolderMoveTarget(folder);
    setFolderMoveParentId("");
    setFolderActionError(null);
  };

  const saveFolderMove = async () => {
    if (!folderMoveTarget || !folderMoveParentId) return;
    const disabledReason = folderMoveParentId === ROOT_FOLDER_VALUE
      ? folderMoveConstraints.rootReason
      : folderMoveConstraints.reasons[folderMoveParentId];
    if (disabledReason) return;
    setBusyAction(`folder:${folderMoveTarget.id}:move`);
    setFolderActionError(null);
    try {
      await adminContentApi.moveCategory(folderMoveTarget.id, {
        target_parent_id: folderMoveParentId === ROOT_FOLDER_VALUE ? null : folderMoveParentId,
        before_category_id: null,
        expected_version: folderMoveTarget.version,
      });
      toast.success(`已移动文件夹“${folderMoveTarget.display_name}”`);
      setFolderMoveTarget(null);
      setFolderMoveParentId("");
      await load(true);
    } catch (moveFailure) {
      setFolderActionError(moveFailure instanceof Error ? moveFailure.message : "移动文件夹失败");
    } finally {
      setBusyAction(null);
    }
  };

  const moveContent = async () => {
    if (!moveTarget || !moveFolderId) return;
    setBusyAction(`${moveTarget.version_id}:move`);
    setMoveError(null);
    try {
      const mode = moveOperation(moveTarget);
      if (mode === "reclassify") {
        await adminContentApi.reclassify(moveTarget.item_id, moveFolderId, moveTarget.version_id);
        toast.success("分类调整任务已提交");
      } else {
        await adminContentApi.move(moveTarget.item_id, moveFolderId, moveTarget.version_id);
        toast.success(mode === "archive" ? "归档目录已调整" : `已移动“${moveTarget.title}”`);
      }
      setMoveTarget(null); setMoveFolderId(""); await load(true);
    } catch (moveFailure) {
      setMoveError(moveFailure instanceof Error ? moveFailure.message : "调整目录失败");
    }
    finally { setBusyAction(null); }
  };

  const moveItemTo = async (item: ManagedContentItem, targetFolderId: string) => {
    if (item.category_id === targetFolderId) return;
    setBusyAction(`${item.version_id}:move`);
    try {
      const mode = moveOperation(item);
      if (mode === "reclassify") await adminContentApi.reclassify(item.item_id, targetFolderId, item.version_id);
      else await adminContentApi.move(item.item_id, targetFolderId, item.version_id);
      toast.success(mode === "archive" ? "归档目录已调整" : mode === "reclassify" ? "分类调整任务已提交" : `已移动“${item.title}”`);
      setDraggedItem(null); await load(true);
    } catch (moveError) { toast.error(moveError instanceof Error ? moveError.message : "调整目录失败"); }
    finally { setBusyAction(null); }
  };

  const requestFolder = async () => {
    if (!currentFolder || !requestFolderName.trim()) return;
    setBusyAction("request-folder");
    try {
      await adminContentApi.createFolderRequest(currentFolder.id, requestFolderName.trim());
      setRequestFolderName(""); setRequestFolderOpen(false); toast.success("目录申请已提交");
    } catch (requestError) { toast.error(requestError instanceof Error ? requestError.message : "提交目录申请失败"); }
    finally { setBusyAction(null); }
  };

  const reviewFolder = async (request: FolderRequest, approved: boolean) => {
    setBusyAction(`folder-request:${request.id}`);
    try {
      await adminContentApi.reviewFolderRequest(request.id, approved);
      toast.success(approved ? "目录申请已批准" : "目录申请已退回"); await load(true);
    } catch (reviewError) { toast.error(reviewError instanceof Error ? reviewError.message : "处理目录申请失败"); }
    finally { setBusyAction(null); }
  };

  const acceptFiles = (incoming: File[]) => {
    const supported = incoming.filter((file) => /\.(pdf|md|docx|xlsx|pptx)$/i.test(file.name));
    setFiles(supported);
    if (supported.length !== incoming.length) toast.error("已忽略不支持的文件格式");
  };

  const openUploadDialog = () => {
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
    try { await operation(); setDetail(null); toast.success(success); await load(true); }
    catch (actionError) { toast.error(actionError instanceof Error ? actionError.message : "操作失败"); }
    finally { setBusyAction(null); }
  };

  const openReviewDialog = (item: ManagedContentItem) => {
    setDetail(null);
    setReviewTarget(item);
    setReviewDecision("approve");
    setReviewNote("");
    setReviewError(null);
  };

  const submitReview = async () => {
    if (!reviewTarget || busyAction === "review") return;
    const approved = reviewDecision === "approve";
    if (!approved && !reviewNote.trim()) {
      setReviewError("退回修改时必须填写原因");
      return;
    }
    setBusyAction("review");
    setReviewError(null);
    try {
      await adminContentApi.review(reviewTarget.version_id, approved, reviewNote);
      setReviewTarget(null);
      toast.success(approved ? "资料已确认" : "资料已退回");
      await load(true);
    } catch (actionError) {
      setReviewError(actionError instanceof Error ? actionError.message : "审核失败，请重试");
    } finally {
      setBusyAction(null);
    }
  };

  const openPublishDialog = (item: ManagedContentItem) => {
    setDetail(null);
    setPublishTarget(item);
    setPublishError(null);
  };

  const publishContent = async () => {
    if (!publishTarget || busyAction === "publish") return;
    const target = publishTarget;
    setBusyAction("publish");
    setPublishError(null);
    try {
      await adminContentApi.publish(target.version_id);
      setPublishTarget(null);
      toast.success("已进入发布队列");
      await load(true);
    } catch (actionError) {
      setPublishError(actionError instanceof Error ? actionError.message : "发布失败，请重试");
    } finally {
      setBusyAction(null);
    }
  };

  const openDeleteDialog = (targets: ManagedContentItem[]) => {
    setDeleteTargets(targets);
    setDeleteAcknowledged(false);
    setDeleteError(null);
  };

  const deleteContent = async () => {
    if (!deleteTargets.length || !deleteAcknowledged) return;
    const targets = deleteTargets;
    setBusyAction("archive");
    setDeleteError(null);
    try {
      const result = targets.length === 1
        ? { results: [{ version_id: targets[0].version_id, status: "succeeded" as const, message: null }], succeeded: 1, failed: 0 }
        : await adminContentApi.bulkArchive(targets.map((item) => ({ item_id: item.item_id, expected_version_id: item.version_id })));
      if (targets.length === 1) await adminContentApi.archive(targets[0].item_id, targets[0].version_id);
      const failures = result.results.filter((entry) => entry.status === "failed");
      setSelected(failures.map((entry) => entry.version_id));
      if (failures.length) {
        const failedVersionIds = new Set(failures.map((entry) => entry.version_id));
        setDeleteTargets(targets.filter((item) => failedVersionIds.has(item.version_id)));
        setDeleteError(`成功 ${result.succeeded} 份，失败 ${result.failed} 份：${failures.map((entry) => entry.message || "请刷新后重试").join("；")}`);
      } else {
        setDeleteTargets([]);
        toast.success(targets.length === 1 ? `已将“${targets[0].title}”移至回收站` : `已将 ${result.succeeded} 份资料移至回收站`);
      }
      await load(true);
    } catch (deleteFailure) {
      setDeleteError(deleteFailure instanceof Error ? deleteFailure.message : "移入回收站失败");
    } finally {
      setBusyAction(null);
    }
  };

  const openRenameDialog = (item: ManagedContentItem) => {
    setRenameTarget(item); setRenameTitle(item.title); setRenameFilename(item.original_filename);
    setRenameConflict(null); setRenameError(null);
  };

  const renameContent = async (replace = false) => {
    if (!renameTarget) return;
    setBusyAction("rename"); setRenameError(null);
    try {
      await adminContentApi.rename(renameTarget.item_id, {
        title: renameTitle.trim(), original_filename: renameFilename.trim(),
        expected_version_id: renameTarget.version_id,
        ...(replace && renameConflict ? {
          replace_conflict_item_id: renameConflict.item_id,
          replace_conflict_expected_version_id: renameConflict.version_id,
        } : {}),
      });
      setRenameTarget(null); setRenameConflict(null);
      toast.success("已创建重命名草稿，请重新提交确认并发布");
      await load(true);
    } catch (renameFailure) {
      const conflict = filenameConflictFrom(renameFailure);
      if (conflict) setRenameConflict(conflict);
      else setRenameError(renameFailure instanceof Error ? renameFailure.message : "重命名失败");
    } finally { setBusyAction(null); }
  };

  const openUpdateDialog = (item: ManagedContentItem) => {
    setUpdateTarget(item); setUpdateFile(null); setUpdateFilenameMode("old");
    setUpdateConflict(null); setUpdateError(null);
    if (updateFileInputRef.current) updateFileInputRef.current.value = "";
  };

  const updateContent = async (replace = false) => {
    if (!updateTarget || !updateFile) return;
    setBusyAction("update"); setUpdateError(null);
    try {
      await adminContentApi.updateVersion(
        updateTarget.item_id, updateFile, updateTarget.version_id, updateFilenameMode,
        replace && updateConflict ? { item_id: updateConflict.item_id, version_id: updateConflict.version_id } : undefined,
      );
      setUpdateTarget(null); setUpdateConflict(null); setUpdateFile(null);
      toast.success("已创建更新草稿，请重新提交确认并发布");
      await load(true);
    } catch (updateFailure) {
      const conflict = filenameConflictFrom(updateFailure);
      if (conflict) setUpdateConflict(conflict);
      else setUpdateError(updateFailure instanceof Error ? updateFailure.message : "更新资料失败");
    } finally { setBusyAction(null); }
  };

  const downloadContent = async (item: ManagedContentItem) => {
    setBusyAction(`${item.version_id}:download`);
    try {
      const result = await adminContentApi.downloadFile(item.version_id, item.original_filename);
      triggerManagedDownload(result.blob, result.filename);
      toast.success(`已开始下载“${item.title}”`);
    } catch (downloadError) {
      toast.error(downloadError instanceof Error ? downloadError.message : "下载资料失败");
    } finally {
      setBusyAction(null);
    }
  };

  const downloadSelected = async () => {
    if (selectedItems.length < 2 || !can("item.download")) return;
    const selectedCount = selectedItems.length;
    setBusyAction("bulk-download");
    toast.info(`正在打包 ${selectedCount} 份资料，请稍候…`, {
      id: BULK_DOWNLOAD_TOAST_ID,
      description: "文件较多时可能需要几秒。",
      duration: Infinity,
    });
    try {
      const result = await adminContentApi.bulkDownload(selectedItems.map((item) => item.version_id));
      triggerManagedDownload(result.blob, result.filename);
      toast.success(`已打包 ${selectedCount} 份资料并开始下载`, { id: BULK_DOWNLOAD_TOAST_ID, duration: 4000 });
    } catch (downloadError) {
      toast.error(downloadError instanceof Error ? downloadError.message : "批量下载失败", { id: BULK_DOWNLOAD_TOAST_ID, duration: 5000 });
    } finally {
      setBusyAction(null);
    }
  };

  const restoreContent = async () => {
    if (!restoreTarget) return;
    const target = restoreTarget;
    setBusyAction(`${target.version_id}:restore`); setRestoreError(null);
    try {
      await adminContentApi.restore(target.item_id, target.version_id);
      setRestoreTarget(null);
      toast.success(`已恢复“${target.title}”`);
      await loadTrash();
    } catch (restoreFailure) {
      setRestoreError(restoreFailure instanceof Error ? restoreFailure.message : "恢复资料失败");
    } finally { setBusyAction(null); }
  };

  const selectedItems = useMemo(
    () => items.filter((item) => selected.includes(item.version_id)),
    [items, selected],
  );

  const canMoveItem = (item: ManagedContentItem) => {
    if (ACTIVE_RECLASSIFICATION_STATUSES.has(item.reclassification_status || "")) return false;
    if (item.content_kind === "media_transcript") return can("item.publish");
    if (item.has_published_head) {
      return can("item.reclassify_published") && item.is_current && item.lifecycle_status === "published";
    }
    return (can("item.move_draft") && ["draft", "rejected"].includes(item.lifecycle_status))
      || (can("item.move_review") && item.lifecycle_status === "awaiting_review");
  };
  const canDeleteItem = (item: ManagedContentItem) => {
    if (item.content_kind === "media_transcript") return false;
    const requiresPublish = item.has_published_head || !["draft", "rejected"].includes(item.lifecycle_status);
    return requiresPublish ? can("item.archive_published") : can("item.archive_draft");
  };

  const executeBulk = async () => {
    if (!bulkAction || selectedItems.length === 0 || busyAction === "bulk") return;
    if (bulkAction === "reject" && !bulkNote.trim()) return;
    setBusyAction("bulk"); setBulkFailures([]);
    try {
      const ids = selectedItems.map((item) => item.version_id);
      const selectedMoveOperation = moveOperation(selectedItems[0]);
      const moveItems = selectedItems.map((item) => ({ item_id: item.item_id, expected_version_id: item.version_id }));
      const result = bulkAction === "move"
        ? selectedMoveOperation === "reclassify"
          ? await adminContentApi.bulkReclassify(moveItems, bulkMoveFolderId)
          : await adminContentApi.bulkMove(moveItems, bulkMoveFolderId)
        : bulkAction === "publish"
        ? await adminContentApi.bulkPublish(ids)
        : await adminContentApi.bulkReview(ids, bulkAction === "approve", bulkNote);
      const titles = new Map(selectedItems.map((item) => [item.version_id, item.title]));
      const failures = result.results
        .filter((entry) => entry.status === "failed")
        .map((entry) => ({ ...entry, title: titles.get(entry.version_id) || "未知资料" }));
      setBulkFailures(failures);
      if (result.failed) toast.error(`成功 ${result.succeeded} 份，失败 ${result.failed} 份`);
      else toast.success(bulkAction === "publish"
        ? `已将 ${result.succeeded} 份资料加入发布队列`
        : bulkAction === "move" && selectedMoveOperation === "reclassify"
          ? `已提交 ${result.succeeded} 份资料的分类调整任务`
          : bulkAction === "move" && selectedMoveOperation === "archive"
            ? `${result.succeeded} 份视频转录稿的归档目录已调整`
            : bulkAction === "move" ? `已移动 ${result.succeeded} 份资料` : `已处理 ${result.succeeded} 份资料`);
      setSelected(failures.map((entry) => entry.version_id)); await load(true);
      if (!result.failed) setBulkAction(null);
    } catch (bulkError) { toast.error(bulkError instanceof Error ? bulkError.message : "批量操作失败"); }
    finally { setBusyAction(null); }
  };

  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const selectable = items.filter((item) => item.content_kind === "document" || canMoveItem(item)).slice(0, BULK_LIMIT);
  const allSelected = selectable.length > 0 && selectable.every((item) => selected.includes(item.version_id));
  const toggleAll = () => setSelected(allSelected ? [] : selectable.map((item) => item.version_id));
  const documentSelection = selectedItems.every((item) => item.content_kind === "document");
  const hasReviewableSelection = documentSelection && selectedItems.some((item) => item.lifecycle_status === "awaiting_review");
  const hasPublishableSelection = documentSelection && selectedItems.some((item) => ["approved", "publication_failed"].includes(item.lifecycle_status));
  const selectedMoveOperations = new Set(selectedItems.map(moveOperation));
  const hasMovableSelection = selectedItems.length > 0
    && selectedItems.every(canMoveItem)
    && selectedMoveOperations.size === 1;
  const bulkMoveLabel = selectedMoveOperations.size === 1 && selectedMoveOperations.has("reclassify")
    ? "批量调整分类"
    : selectedMoveOperations.size === 1 && selectedMoveOperations.has("archive")
      ? "批量调整归档目录"
      : "批量移动资料";
  const hasDeletableSelection = documentSelection && selectedItems.some(canDeleteItem);
  const hasDownloadableSelection = documentSelection && selectedItems.length > 1 && can("item.download");
  const bulkDisabled = Boolean(busyAction) || refreshing || !enabled;

  const renderItemStatus = (item: ManagedContentItem) => {
    if (ACTIVE_RECLASSIFICATION_STATUSES.has(item.reclassification_status || "")) {
      return <Badge variant="warning">分类调整中</Badge>;
    }
    if (item.reclassification_status === "failed") {
      return <Badge variant="destructive">分类调整失败</Badge>;
    }
    return <Badge variant={statusVariant(item.lifecycle_status)}>{statusLabel[item.lifecycle_status] || "未知状态"}</Badge>;
  };

  const renderActions = (item: ManagedContentItem) => {
    const disabled = Boolean(busyAction) || refreshing || !enabled;
    const isMediaTranscript = item.content_kind === "media_transcript";
    const reclassifying = ACTIVE_RECLASSIFICATION_STATUSES.has(item.reclassification_status || "");
    const previewable = Boolean(item.preview_parent_id && ["pdf", "docx", "xlsx", "pptx"].includes(item.doc_type));
    const movable = canMoveItem(item);
    const downloadable = !isMediaTranscript && can("item.download");
    const revisionAllowed = !isMediaTranscript && can("item.upload") && item.lifecycle_status !== "publishing" && !reclassifying;
    const deletable = canDeleteItem(item) && item.lifecycle_status !== "publishing" && !reclassifying;
    const workflow = item.lifecycle_status === "draft" && can("item.submit")
      ? { label: "提交", action: () => void act(item, "submit", () => adminContentApi.submit(item.version_id), "已提交确认") }
      : item.lifecycle_status === "rejected" && can("item.submit")
        ? { label: "重新提交", action: () => void act(item, "submit", () => adminContentApi.submit(item.version_id), "已重新提交确认") }
        : item.lifecycle_status === "awaiting_review" && can("item.review")
          ? { label: "审核", action: () => openReviewDialog(item) }
          : item.lifecycle_status === "approved" && can("item.publish")
            ? { label: "发布", action: () => openPublishDialog(item) }
            : item.lifecycle_status === "publication_failed" && can("item.publish")
              ? { label: "重新发布", action: () => openPublishDialog(item) }
              : null;
    const unavailableReason = busyAction
      ? "正在处理其他操作，请稍候"
      : refreshing
        ? "资料列表正在刷新，请稍候"
        : !enabled
          ? "资料管理功能当前不可用"
          : null;
    if (isMediaTranscript) {
      const moveTooltip = unavailableReason || (movable ? "调整归档目录" : "当前账号没有发布权限");
      return <div className="ml-auto flex min-h-10 w-[10.5rem] items-center justify-end gap-1">
        <IconButton label={`查看“${item.title}”的详细信息`} tooltip={unavailableReason || "查看视频转录稿详情"} className="border border-border max-sm:size-10" disabled={disabled} onClick={() => setDetail(item)}><Info className="size-4" /></IconButton>
        <IconButton label={`播放“${item.title}”`} tooltip={unavailableReason || (item.media_id ? "播放视频与转录稿" : "媒体关联缺失，暂无法播放")} className="border border-border max-sm:size-10" disabled={disabled || !item.media_id} onClick={() => openVideoPreview({ mediaId: item.media_id!, title: item.title, startSeconds: 0, fromSource: false })}><Film className="size-4" /></IconButton>
        <IconButton label={`调整“${item.title}”的归档目录`} tooltip={moveTooltip} className="border border-border max-sm:size-10" disabled={disabled || !movable} onClick={() => { setMoveTarget(item); setMoveFolderId(""); setMoveError(null); }}><FolderInput className="size-4" /></IconButton>
        <a aria-label={`在视频管理中打开“${item.title}”`} title={`在视频管理中打开“${item.title}”`} className={buttonVariants({ variant: "outline", size: "icon", className: "!size-9 max-sm:!size-10" })} href={`/admin/media?media_id=${encodeURIComponent(item.media_id || "")}&workbench=1`}><ExternalLink className="size-4" /></a>
      </div>;
    }
    const previewTooltip = unavailableReason
      || (previewable
        ? "预览文件"
        : !item.preview_parent_id
          ? "该资料尚未生成可预览文件"
          : "当前文件格式暂不支持在线预览");
    const moveTooltip = unavailableReason
      || (movable
        ? item.has_published_head ? "调整分类" : "移动资料"
        : ACTIVE_RECLASSIFICATION_STATUSES.has(item.reclassification_status || "")
          ? "分类调整正在同步索引和目录"
        : item.has_published_head
          ? item.is_current && item.lifecycle_status === "published"
            ? "当前账号没有调整已发布资料分类的权限"
            : "存在待处理的新版本，暂时不能调整正式分类"
          : !["draft", "rejected", "awaiting_review"].includes(item.lifecycle_status)
            ? "仅草稿、已退回或待确认的资料可以移动"
            : item.lifecycle_status === "awaiting_review"
              ? "当前账号没有移动待确认资料的权限"
              : "当前账号没有移动草稿或已退回资料的权限");
    const revisionTooltip = unavailableReason
      || (revisionAllowed
        ? "重命名资料"
        : item.lifecycle_status === "publishing"
          ? "资料正在发布，暂不能重命名"
          : reclassifying
            ? "资料正在调整分类，暂不能重命名"
          : "当前账号没有上传和修改资料的权限");
    const updateTooltip = unavailableReason
      || (revisionAllowed
        ? "更新资料文件"
        : item.lifecycle_status === "publishing"
          ? "资料正在发布，暂不能更新文件"
          : reclassifying
            ? "资料正在调整分类，暂不能更新文件"
          : "当前账号没有上传和修改资料的权限");
    const deleteTooltip = unavailableReason
      || (deletable
        ? "移入回收站"
        : item.lifecycle_status === "publishing"
          ? "资料正在发布，暂不能移入回收站"
          : reclassifying
            ? "资料正在调整分类，暂不能移入回收站"
          : item.has_published_head || !["draft", "rejected"].includes(item.lifecycle_status)
            ? "当前账号没有删除已审核或已发布资料的权限"
            : "当前账号没有删除草稿或已退回资料的权限");
    return <div className="ml-auto flex w-full flex-col items-stretch gap-2 lg:w-auto lg:flex-row lg:items-center">
      {workflow && <Button size="sm" className="w-full shrink-0 max-sm:h-10 lg:w-auto" disabled={disabled} onClick={workflow.action}>{workflow.label}</Button>}
      <div className="ml-auto flex min-h-10 w-[19rem] max-w-full items-center justify-end gap-1 sm:w-[17.25rem]">
        <IconButton label={`查看“${item.title}”的详细信息`} tooltip={unavailableReason || "查看资料详情"} className="border border-border max-sm:size-10" disabled={disabled} onClick={() => setDetail(item)}><Info className="size-4" /></IconButton>
        <IconButton label={`预览“${item.title}”`} tooltip={previewTooltip} className="border border-border max-sm:size-10" disabled={disabled || !previewable} onClick={() => openDocumentPreview(item.preview_parent_id!, item.title, item.doc_type, 1, {}, null)}><Eye className="size-4" /></IconButton>
        <IconButton label={item.has_published_head ? `调整“${item.title}”的分类` : `移动“${item.title}”`} tooltip={moveTooltip} className="border border-border max-sm:size-10" disabled={disabled || !movable} onClick={() => { setMoveTarget(item); setMoveFolderId(""); setMoveError(null); }}><FolderInput className="size-4" /></IconButton>
        <IconButton label={`下载“${item.title}”`} tooltip={unavailableReason || (downloadable ? "下载文件" : "当前账号没有下载文件的权限")} className="border border-border max-sm:size-10" disabled={disabled || !downloadable} onClick={() => void downloadContent(item)}><Download className="size-4" /></IconButton>
        <IconButton label={`重命名“${item.title}”`} tooltip={revisionTooltip} className="border border-border max-sm:size-10" disabled={disabled || !revisionAllowed} onClick={() => openRenameDialog(item)}><Pencil className="size-4" /></IconButton>
        <IconButton label={`更新“${item.title}”`} tooltip={updateTooltip} className="border border-border max-sm:size-10" disabled={disabled || !revisionAllowed} onClick={() => openUpdateDialog(item)}><FileUp className="size-4" /></IconButton>
        <IconButton label={`删除“${item.title}”`} tooltip={deleteTooltip} className="border border-destructive/30 text-destructive hover:bg-destructive/10 hover:text-destructive max-sm:size-10" disabled={disabled || !deletable} onClick={() => openDeleteDialog([item])}><Trash2 className="size-4" /></IconButton>
      </div>
    </div>;
  };

  const renderFolderActions = (folder: ManagedCategory) => {
    const folderLabel = `${folder.display_code} ${folder.display_name}`;
    const disabled = Boolean(busyAction) || refreshing || !enabled;
    const unavailableReason = busyAction
      ? "正在处理其他操作，请稍候"
      : refreshing
        ? "资料列表正在刷新，请稍候"
        : !enabled
          ? "资料管理功能当前不可用"
          : null;
    return <div className="ml-auto flex min-h-10 shrink-0 items-center justify-end gap-1" onClick={(event) => event.stopPropagation()}>
      {can("category.manage") && <>
        <IconButton label={`设置文件夹“${folderLabel}”的顺序`} tooltip={unavailableReason || "设置排序序号"} className="border border-border max-sm:size-10" disabled={disabled} onClick={() => openFolderSort(folder)}><ListOrdered className="size-4" /></IconButton>
        <IconButton label={`移动文件夹“${folderLabel}”`} tooltip={unavailableReason || "移动文件夹位置"} className="border border-border max-sm:size-10" disabled={disabled} onClick={() => openFolderMove(folder)}><FolderInput className="size-4" /></IconButton>
        <IconButton label={`重命名文件夹“${folderLabel}”`} tooltip={unavailableReason || "重命名文件夹"} className="border border-border max-sm:size-10" disabled={disabled} onClick={() => openFolderRename(folder)}><Pencil className="size-4" /></IconButton>
      </>}
      <IconButton label={`打开文件夹“${folderLabel}”`} tooltip={unavailableReason || "打开文件夹"} className="border border-border max-sm:size-10" disabled={disabled} onClick={() => setCurrentFolderId(folder.id)}><ChevronRight className="size-4" /></IconButton>
    </div>;
  };

  const selectView = (nextView: ManagedContentView) => {
    setView(nextView);
    if (nextView === "library" || nextView === "trash") setPage(0);
    if (nextView !== "library") setSelected([]);
  };
  const viewTabs = (can("trash.view") || can("item.upload") || can("index.view")) && (
    <div className="flex flex-wrap gap-2" role="tablist" aria-label="资料视图">
      <Button size="sm" variant={view === "library" ? "default" : "outline"} role="tab" aria-selected={view === "library"} onClick={() => selectView("library")}>资料库</Button>
      {can("trash.view") && <Button size="sm" variant={view === "trash" ? "default" : "outline"} role="tab" aria-selected={view === "trash"} onClick={() => selectView("trash")}>回收站</Button>}
      {can("item.upload") && <Button size="sm" variant={view === "uploads" ? "default" : "outline"} role="tab" aria-selected={view === "uploads"} onClick={() => selectView("uploads")}><Upload className="size-4" />上传任务</Button>}
      {can("index.view") && <Button size="sm" variant={view === "index" ? "default" : "outline"} role="tab" aria-selected={view === "index"} onClick={() => selectView("index")}><ListChecks className="size-4" />索引任务</Button>}
    </div>
  );

  if (view === "index") {
    return <section className="space-y-5" aria-labelledby="managed-content-title">
      <header><p className="text-ui-xs font-medium text-primary">内容管理</p><h1 id="managed-content-title" className="mt-1 text-ui-2xl font-semibold tracking-tight">资料管理</h1><p className="mt-1 text-ui-sm text-muted-foreground">统一管理资料的上传、分类、确认和发布。</p></header>
      {viewTabs}
      <AdminDocumentsPage embedded />
    </section>;
  }

  if (view === "uploads") {
    return <section className="space-y-5" aria-labelledby="managed-content-title">
      <header><p className="text-ui-xs font-medium text-primary">内容管理</p><h1 id="managed-content-title" className="mt-1 text-ui-2xl font-semibold tracking-tight">资料管理</h1><p className="mt-1 text-ui-sm text-muted-foreground">统一管理资料的上传、分类、确认和发布。</p></header>
      {viewTabs}
      <UploadTasksPanel activeUpload={activeUpload} canRetry={(task) => Boolean(lastUploadAttempt?.batchId === task.batch_id)} onRetry={(task) => void retryUploadTask(task)} />
    </section>;
  }

  if (view === "trash") {
    const trashPageCount = Math.max(1, Math.ceil(trashTotal / PAGE_SIZE));
    return <section className="space-y-5" aria-labelledby="managed-content-title">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between"><div><p className="text-ui-xs font-medium text-primary">内容管理</p><h1 id="managed-content-title" className="mt-1 text-ui-2xl font-semibold tracking-tight">回收站</h1><p className="mt-1 text-ui-sm text-muted-foreground">{can("trash.restore") ? "查看和恢复已移出资料库的资料。" : "查看已移出资料库的资料。"}</p></div><Button size="sm" variant="outline" onClick={() => void loadTrash()} disabled={trashLoading}><RefreshCw className={trashLoading ? "size-4 animate-spin" : "size-4"} />刷新</Button></header>
      {viewTabs}
      {error && <ErrorState title="回收站加载失败" description={error} action={<Button size="sm" variant="outline" onClick={() => void loadTrash()}>重新加载</Button>} />}
      <Card className="overflow-hidden shadow-surface [&_table]:!min-w-[58rem]"><div className="flex flex-col gap-3 border-b border-border px-4 py-4 sm:flex-row sm:items-end sm:justify-between sm:px-5"><label className="max-w-xl flex-1 space-y-1 text-ui-xs text-muted-foreground"><span>搜索回收站</span><span className="relative block"><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2" /><Input className="pl-9" value={queryInput} onChange={(event) => setQueryInput(event.target.value)} placeholder="搜索名称、文件名、原目录或上传路径…" /></span></label><p className="text-ui-xs text-muted-foreground">共 {trashTotal} 份</p></div>
        {trashLoading ? <LoadingState className="min-h-48 border-0" label="正在加载回收站…" /> : trashItems.length === 0 ? <EmptyState className="rounded-none border-0" title="回收站为空" description="移至回收站的资料会显示在这里。" /> : <>
          <div className="hidden overflow-x-auto lg:block"><table className="w-full text-ui-sm"><thead className="border-b border-border bg-surface-muted text-left text-muted-foreground"><tr><th className="px-4 py-3 font-medium">资料</th><th className="px-4 py-3 font-medium">原目录</th><th className="px-4 py-3 font-medium">原状态</th><th className="px-4 py-3 font-medium">来源</th><th className="px-4 py-3 font-medium">移入回收站</th>{can("trash.restore") && <th className="px-4 py-3 text-right font-medium">操作</th>}</tr></thead><tbody className="divide-y divide-border">{trashItems.map((item) => { const previousStatus = item.pre_archive_lifecycle_status || item.lifecycle_status; const sourcePath = item.source_rel_path && item.source_rel_path !== item.original_filename ? item.source_rel_path : null; return <tr key={item.item_id} className="align-top transition-colors duration-normal hover:bg-surface-muted/60"><td className="max-w-xs px-4 py-3"><p className="break-words font-medium">{item.title}</p><p className="mt-1 break-all text-ui-xs text-muted-foreground">{item.original_filename} · v{item.version_number}</p></td><td className="max-w-sm px-4 py-3"><p className="break-words">{item.category_path || item.category_label}</p>{sourcePath && <p className="mt-1 break-all text-ui-xs text-muted-foreground">上传路径：{sourcePath}</p>}</td><td className="px-4 py-3"><Badge variant={statusVariant(previousStatus)}>{statusLabel[previousStatus] || "未知状态"}</Badge></td><td className="px-4 py-3">{sourceLabel[item.source_origin] || "其他来源"}</td><td className="px-4 py-3"><p>{item.archived_by_name || "未知人员"}</p><p className="mt-1 whitespace-nowrap text-ui-xs text-muted-foreground">{formatAdminDate(item.archived_at)}</p></td>{can("trash.restore") && <td className="px-4 py-3 text-right"><Button size="sm" variant="outline" disabled={Boolean(busyAction)} onClick={() => { setRestoreError(null); setRestoreTarget(item); }}><ArchiveRestore className="size-4" />恢复</Button></td>}</tr>; })}</tbody></table></div>
          <ul className="divide-y divide-border lg:hidden">{trashItems.map((item) => { const previousStatus = item.pre_archive_lifecycle_status || item.lifecycle_status; const sourcePath = item.source_rel_path && item.source_rel_path !== item.original_filename ? item.source_rel_path : null; return <li key={item.item_id} className="space-y-3 px-4 py-4 sm:px-5"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><p className="break-words font-medium">{item.title}</p><p className="mt-1 break-all text-ui-xs text-muted-foreground">{item.original_filename} · v{item.version_number}</p></div><Badge className="shrink-0" variant={statusVariant(previousStatus)}>{statusLabel[previousStatus] || "未知状态"}</Badge></div><dl className="grid grid-cols-[5rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-ui-sm"><dt className="text-muted-foreground">原目录</dt><dd className="break-words">{item.category_path || item.category_label}</dd>{sourcePath && <><dt className="text-muted-foreground">上传路径</dt><dd className="break-all">{sourcePath}</dd></>}<dt className="text-muted-foreground">来源</dt><dd>{sourceLabel[item.source_origin] || "其他来源"}</dd><dt className="text-muted-foreground">移入人员</dt><dd>{item.archived_by_name || "未知人员"}</dd><dt className="text-muted-foreground">移入时间</dt><dd>{formatAdminDate(item.archived_at)}</dd></dl>{can("trash.restore") && <Button size="sm" variant="outline" className="w-full sm:w-auto" disabled={Boolean(busyAction)} onClick={() => { setRestoreError(null); setRestoreTarget(item); }}><ArchiveRestore className="size-4" />恢复</Button>}</li>; })}</ul>
        </>}
        <div className="flex items-center justify-between border-t border-border px-4 py-3 sm:px-5"><p className="text-ui-xs text-muted-foreground">第 {page + 1} / {trashPageCount} 页</p><div className="flex gap-2"><Button size="sm" variant="outline" disabled={page === 0 || trashLoading} onClick={() => setPage((value) => value - 1)}>上一页</Button><Button size="sm" variant="outline" disabled={page + 1 >= trashPageCount || trashLoading} onClick={() => setPage((value) => value + 1)}>下一页</Button></div></div>
      </Card>
      <Dialog open={Boolean(restoreTarget)} onOpenChange={(open) => { if (!open && !busyAction) { setRestoreTarget(null); setRestoreError(null); } }}><DialogContent><DialogHeader><DialogTitle>恢复资料</DialogTitle><DialogDescription>“{restoreTarget?.title}”将恢复到资料库。已发布或发布失败的资料会恢复为“已确认”，需要具备发布权限的人员重新发布后才会进入检索。</DialogDescription></DialogHeader>{restoreError && <p className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui-sm text-destructive" role="alert">{restoreError}</p>}<DialogFooter><Button variant="outline" disabled={Boolean(busyAction)} onClick={() => setRestoreTarget(null)}>取消</Button><Button disabled={Boolean(busyAction)} onClick={() => void restoreContent()}>{busyAction ? "恢复中…" : "确认恢复"}</Button></DialogFooter></DialogContent></Dialog>
    </section>;
  }

  return <section className="space-y-5" aria-labelledby="managed-content-title">
    <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div><p className="text-ui-xs font-medium text-primary">内容管理</p><h1 id="managed-content-title" className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">资料管理</h1><p className="mt-1 text-ui-sm text-muted-foreground">统一管理资料的上传、分类、确认和发布。</p></div>
    </header>

    {viewTabs}

    <section className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="资料状态概览">
      {[["全部资料", Object.values(counts).reduce((sum, value) => sum + value, 0)], ["待确认", counts.awaiting_review || 0], ["已确认", counts.approved || 0], ["已发布", counts.published || 0]].map(([label, value]) => <Card key={label} className="overflow-hidden shadow-surface"><CardContent className="relative p-4 pt-4"><span className="absolute inset-x-0 top-0 h-1 bg-primary/80" aria-hidden="true" /><p className="text-ui-xs font-medium text-muted-foreground">{label}</p><p className="mt-2 text-ui-xl font-semibold tabular-nums text-foreground">{value}</p></CardContent></Card>)}
    </section>

    {!enabled && !loading && <div className="border border-warning/40 bg-warning/10 px-4 py-3 text-ui-sm" role="status">资料管理当前未启用，上传和流程操作暂不可用。</div>}
    {error && <ErrorState title="资料列表加载失败" description={error} action={<Button size="sm" variant="outline" onClick={() => void load()}>重新加载</Button>} />}

    {can("folder.review") && folderRequests.length > 0 && <Card className="overflow-hidden shadow-surface" aria-labelledby="folder-requests-title"><div className="border-b border-border px-4 py-3 sm:px-5"><h2 id="folder-requests-title" className="text-ui-base font-semibold">待处理目录申请</h2></div><ul className="divide-y divide-border">{folderRequests.map((request) => <li key={request.id} className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5"><div className="min-w-0"><p className="break-words text-ui-sm font-medium">{request.display_name}</p><p className="mt-0.5 text-ui-xs text-muted-foreground">上级目录：{request.parent_label} · 申请人：{request.requester_name || "未知"}</p></div><div className="flex gap-2"><Button size="sm" variant="outline" disabled={busyAction === `folder-request:${request.id}`} onClick={() => void reviewFolder(request, false)}><X className="size-4" />退回</Button><Button size="sm" disabled={busyAction === `folder-request:${request.id}`} onClick={() => void reviewFolder(request, true)}><Check className="size-4" />批准</Button></div></li>)}</ul></Card>}
    <Card className="shadow-surface [&_table]:!min-w-[56rem]" aria-labelledby="managed-list-title">
      <div className="grid gap-3 border-b border-border px-4 py-4 xl:grid-cols-[minmax(13rem,1fr)_18rem_auto] xl:items-end min-[1400px]:grid-cols-[minmax(13rem,1fr)_24rem_auto] sm:px-5">
        <div className="min-w-0"><h2 id="managed-list-title" className="text-ui-base font-semibold">资料列表</h2><p className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-ui-xs text-muted-foreground"><span>共 {total} 份</span><span role="status" aria-live="polite">· {selected.length > 0 ? <>已选择 <strong>{selected.length}</strong> 份，单次最多 {BULK_LIMIT} 份</> : <>未选择资料，单次最多 {BULK_LIMIT} 份</>}</span></p></div>
        <ManagedContentSearchFilters
          queryInput={queryInput}
          statusFilter={statusFilter}
          sourceFilter={sourceFilter}
          kindFilter={kindFilter}
          disabled={!currentFolderId}
          onQueryInputChange={setQueryInput}
          onStatusFilterChange={setStatusFilter}
          onSourceFilterChange={setSourceFilter}
          onKindFilterChange={setKindFilter}
          onClear={() => { setQueryInput(""); setStatusFilter(""); setSourceFilter(""); setKindFilter(""); }}
        />
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {can("item.upload") && <Button size="sm" className="max-sm:h-control-md" onClick={openUploadDialog} disabled={!enabled || !currentFolderId || uploading || folderScanning}><Upload className="size-4" />{folderScanning ? "读取文件夹中…" : "上传文件"}</Button>}
          <Button size="sm" variant="outline" className="max-sm:h-control-md" onClick={() => void load(true)} disabled={loading || refreshing}><RefreshCw className={refreshing ? "size-4 animate-spin" : "size-4"} />{refreshing ? "刷新中…" : "刷新列表"}</Button>
          {selected.length > 1 ? <BatchActionsMenu disabled={bulkDisabled} options={[
            { key: "move", label: bulkMoveLabel, icon: <FolderInput className="size-4" />, disabled: !hasMovableSelection, disabledReason: "所选资料必须属于同一状态且都可调整目录", onSelect: () => { setBulkFailures([]); setBulkMoveFolderId(""); setBulkNote(""); setBulkAction("move"); } },
            { key: "approve", label: "批量确认", icon: <Check className="size-4" />, disabled: !can("item.review") || !hasReviewableSelection, disabledReason: "仅文档支持批量确认，且至少包含一份待确认文档", onSelect: () => { setBulkFailures([]); setBulkNote(""); setBulkAction("approve"); } },
            { key: "reject", label: "批量退回", icon: <X className="size-4" />, disabled: !can("item.review") || !hasReviewableSelection, disabledReason: "仅文档支持批量退回，且至少包含一份待确认文档", onSelect: () => { setBulkFailures([]); setBulkNote(""); setBulkAction("reject"); } },
            { key: "publish", label: "批量发布", icon: <Rocket className="size-4" />, disabled: !can("item.publish") || !hasPublishableSelection, disabledReason: "仅文档支持此处发布，视频转录稿请前往视频管理", onSelect: () => { setBulkFailures([]); setBulkNote(""); setBulkAction("publish"); } },
            { key: "download", label: "批量下载", icon: <Download className="size-4" />, disabled: !hasDownloadableSelection, disabledReason: "仅文档支持批量下载，且需要下载权限", onSelect: () => { void downloadSelected(); } },
            { key: "archive", label: "批量删除", icon: <Trash2 className="size-4" />, disabled: !hasDeletableSelection, disabledReason: "视频转录稿需在视频管理中删除", destructive: true, onSelect: () => openDeleteDialog(selectedItems) },
          ]} /> : (can("folder.request") || can("category.manage")) && <Button size="sm" variant="outline" className="max-sm:h-control-md" onClick={() => can("category.manage") ? setNewFolderOpen(true) : setRequestFolderOpen(true)} disabled={!currentFolder || currentFolder.level >= 4}><FolderPlus className="size-4" />新建目录</Button>}
        </div>
      </div>
      <div className="border-b border-border bg-surface-muted/40 px-4 py-3 sm:px-5" data-testid="managed-folder-address"><nav className="flex min-w-0 items-center gap-1 rounded-ui-md border border-input bg-background px-3 py-2 text-ui-sm" aria-label="资料路径"><button type="button" className="shrink-0 rounded px-1 py-0.5 font-medium hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => setCurrentFolderId("")}>/</button>{breadcrumbs.map((folder) => <span key={folder.id} className="flex min-w-0 items-center gap-1"><ChevronRight className="size-4 shrink-0 text-muted-foreground" /><button type="button" className="max-w-56 truncate rounded px-1 py-0.5 hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => setCurrentFolderId(folder.id)}>{folder.display_code} {folder.display_name}</button></span>)}</nav></div>

      <div data-testid="managed-content-drop-list" className="relative" onDragEnter={handleListDragEnter} onDragOver={handleListDragOver} onDragLeave={handleListDragLeave} onDrop={handleListDrop}>
      {listDropActive && <div data-testid="managed-content-drop-overlay" className="pointer-events-none absolute inset-1 z-sticky rounded-ui-lg border-2 border-dashed border-primary/70 bg-background/70 text-center shadow-focus backdrop-blur-[1px]" role="status" aria-live="polite"><div className="absolute left-1/2 flex w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-3" style={{ top: listDropPromptTop }}><span className="flex size-12 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-surface" aria-hidden="true"><Upload className="size-6" /></span><div className="space-y-1"><p className="break-words text-ui-base font-semibold">松开以上传文件到“{currentFolderDropLabel}”</p><p className="text-ui-xs text-muted-foreground">也支持拖入文件夹；支持 PDF、Markdown、Word、Excel 和 PPT 文件</p></div></div></div>}
      {loading ? <LoadingState className="min-h-48 border-x-0 border-b-0" label="正在加载资料…" /> : !error && items.length === 0 && childFolders.length === 0 ? <EmptyState className="min-h-56 rounded-none border-x-0 border-b-0 sm:min-h-64" title="没有符合条件的资料" description="请调整筛选条件或上传新资料。" /> : !error && <>
        <div className="hidden overflow-x-auto border-t border-border lg:block"><table className="w-full min-w-[80rem] text-ui-sm"><thead className="border-b border-border bg-surface-muted text-left text-muted-foreground"><tr><th className="w-12 px-3 py-3"><Checkbox aria-label="选择当前页前20份资料" checked={allSelected} onChange={toggleAll} /></th>{([ ["docType", "类型"], ["folderOrder", "顺序"], ["title", "资料"], ["updatedAt", "更新时间"], ["status", "状态"], ["source", "来源"] ] as [SortKey, string][]).map(([key, label]) => <th key={key} aria-sort={sort?.key === key ? sort.direction === "asc" ? "ascending" : "descending" : "none"} className={key === "docType" ? "w-24 px-3 py-3 text-center font-medium" : key === "folderOrder" ? "w-20 px-3 py-3 text-center font-medium" : "px-3 py-3 font-medium"}><button type="button" className="inline-flex items-center gap-1 rounded px-1 py-0.5 hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => toggleSort(key)}>{label}{sortIcon(key)}</button></th>)}<th className="px-3 py-3 text-right font-medium">操作</th></tr></thead><tbody className="divide-y divide-border">
          {sortedChildFolders.map((folder) => {
            const folderLabel = `${folder.display_code} ${folder.display_name}`;
            return <tr key={folder.id} data-testid={`managed-folder-row-${folder.id}`} className={`cursor-pointer transition-colors duration-normal hover:bg-surface-muted/60 ${draggedItem ? "bg-primary/5 outline outline-1 -outline-offset-1 outline-primary/50" : ""}`} onClick={() => setCurrentFolderId(folder.id)} onDragOver={(event) => { if (draggedItem) { event.preventDefault(); event.stopPropagation(); } }} onDrop={(event) => { if (!draggedItem) return; event.preventDefault(); event.stopPropagation(); void moveItemTo(draggedItem, folder.id); }}><td className="px-3 py-3" /><td className="px-3 py-3"><ManagedItemType folder /></td><td className="px-3 py-3 text-center tabular-nums">{folder.sort_order > 0 ? folder.sort_order : "—"}</td><td className="max-w-xs px-3 py-3"><button type="button" className="block max-w-full rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => setCurrentFolderId(folder.id)}><span className="block break-words font-medium">{folderLabel}</span><span className="mt-0.5 block text-ui-xs text-muted-foreground">{folder.item_count} 份直接资料</span></button></td><td className="whitespace-nowrap px-3 py-3 tabular-nums">{formatManagedUpdatedAt(folder.updated_at)}</td><td className="px-3 py-3 text-muted-foreground">—</td><td className="px-3 py-3 text-muted-foreground">—</td><td className="px-3 py-3 text-right">{renderFolderActions(folder)}</td></tr>;
          })}
          {sortedItems.map((item, index) => { const movable = canMoveItem(item); const draggable = movable && moveOperation(item) !== "reclassify"; const rowSelectable = item.content_kind === "document" || movable; return <tr key={item.item_id} draggable={draggable} title={draggable ? "拖动到文件夹行可调整目录" : undefined} onDragStart={() => setDraggedItem(item)} onDragEnd={() => setDraggedItem(null)} className={`transition-colors duration-normal hover:bg-surface-muted/60 ${draggable ? "cursor-grab" : ""}`}><td className="px-3 py-3"><Checkbox aria-label={`选择${item.title}`} checked={selected.includes(item.version_id)} disabled={index >= BULK_LIMIT || !rowSelectable} title={!rowSelectable ? "视频转录稿需要发布权限才能批量调整归档目录" : undefined} onChange={() => setSelected((current) => current.includes(item.version_id) ? current.filter((id) => id !== item.version_id) : [...current, item.version_id].slice(0, BULK_LIMIT))} /></td><td className="px-3 py-3"><ManagedItemType docType={item.doc_type} /></td><td className="px-3 py-3 text-center text-muted-foreground">—</td><td className="max-w-xs px-3 py-3"><ManagedItemIdentity item={item} /></td><td className="whitespace-nowrap px-3 py-3 tabular-nums">{formatManagedUpdatedAt(item.updated_at)}</td><td className="px-3 py-3">{renderItemStatus(item)}</td><td className="px-3 py-3">{sourceLabel[item.source_origin] || "其他来源"}</td><td className="px-3 py-3 text-right">{renderActions(item)}</td></tr>; })}</tbody></table></div>
        <ul className="divide-y divide-border border-t border-border lg:hidden">{sortedChildFolders.map((folder) => {
          const folderLabel = `${folder.display_code} ${folder.display_name}`;
          return <li key={folder.id} data-testid={`managed-folder-mobile-${folder.id}`} className="min-h-16 space-y-2 px-4 py-3 sm:px-5"><button type="button" className="flex w-full min-w-0 items-center gap-3 rounded-ui-md text-left transition-colors hover:bg-surface-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => setCurrentFolderId(folder.id)}><Folder className="size-5 shrink-0 text-primary" aria-hidden="true" /><span className="min-w-0 flex-1"><span className="block break-words font-medium">{folderLabel}</span><span className="mt-0.5 block text-ui-xs text-muted-foreground">排序 {folder.sort_order > 0 ? folder.sort_order : "未设置"} · {folder.item_count} 份直接资料</span></span></button>{renderFolderActions(folder)}</li>;
        })}{sortedItems.map((item, index) => { const rowSelectable = item.content_kind === "document" || canMoveItem(item); return <li key={item.item_id} className="space-y-3 px-4 py-4 sm:px-5"><div className="flex items-start gap-3"><Checkbox className="mt-0.5" aria-label={`选择${item.title}`} checked={selected.includes(item.version_id)} disabled={index >= BULK_LIMIT || !rowSelectable} title={!rowSelectable ? "视频转录稿需要发布权限才能批量调整归档目录" : undefined} onChange={() => setSelected((current) => current.includes(item.version_id) ? current.filter((id) => id !== item.version_id) : [...current, item.version_id].slice(0, BULK_LIMIT))} /><ManagedItemType docType={item.doc_type} /><div className="min-w-0 flex-1"><ManagedItemIdentity item={item} /></div></div><dl className="grid grid-cols-[4rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-ui-sm"><dt className="text-muted-foreground">状态</dt><dd>{renderItemStatus(item)}</dd><dt className="text-muted-foreground">更新时间</dt><dd className="whitespace-nowrap tabular-nums">{formatManagedUpdatedAt(item.updated_at)}</dd><dt className="text-muted-foreground">来源</dt><dd>{sourceLabel[item.source_origin] || "其他来源"}</dd></dl>{renderActions(item)}</li>; })}</ul>
        <div className="flex flex-col gap-2 border-t border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5"><p className="text-ui-xs text-muted-foreground">共 {total} 份，第 {page + 1} / {pageCount} 页</p><div className="flex flex-wrap items-center justify-end gap-2"><label className="flex items-center gap-2 text-ui-xs text-muted-foreground">每页<Select aria-label="每页条数" className="h-control-sm w-20" value={String(pageSize)} onChange={(event) => setPageSize(Number(event.target.value))}>{PAGE_SIZE_OPTIONS.map((size) => <option key={size} value={size}>{size} 条</option>)}</Select></label><Button size="sm" variant="outline" disabled={page === 0 || loading} onClick={() => setPage((value) => value - 1)}>上一页</Button><Select aria-label="跳转页码" className="h-control-sm w-24" value={String(page + 1)} onChange={(event) => setPage(Number(event.target.value) - 1)} disabled={loading}>{Array.from({ length: pageCount }, (_, index) => <option key={index + 1} value={index + 1}>第 {index + 1} 页</option>)}</Select><Button size="sm" variant="outline" disabled={page + 1 >= pageCount || loading} onClick={() => setPage((value) => value + 1)}>下一页</Button></div></div>
      </>}
      </div>
    </Card>

    <Dialog open={Boolean(folderSortTarget)} onOpenChange={(open) => {
      if (!open && !busyAction?.startsWith("folder:")) {
        setFolderSortTarget(null);
        setFolderActionError(null);
      }
    }}>
      <DialogContent>
        <DialogHeader><DialogTitle>设置文件夹顺序</DialogTitle><DialogDescription>排序序号只控制同级文件夹的显示顺序，不会改变地址栏中的显示编号。</DialogDescription></DialogHeader>
        {folderSortTarget && <div className="space-y-4">
          <div className="rounded-ui-md border border-border bg-surface-muted/40 px-3 py-2 text-ui-sm"><p className="text-ui-xs text-muted-foreground">当前路径</p><p className="mt-1 break-words font-medium">{folderSortTarget.full_path}</p></div>
          <label className="block space-y-1.5 text-ui-sm font-medium"><span>排序序号</span><Input type="number" min={0} max={999999} step={1} value={folderSortValue} onChange={(event) => { setFolderSortValue(event.target.value); setFolderActionError(null); }} placeholder="留空表示未设置" aria-label="排序序号" /><span className="block text-ui-xs font-normal text-muted-foreground">建议使用 10、20、30 等间隔值，后续插入更方便。</span></label>
          {!folderSortValid && <p className="text-ui-sm text-destructive" role="alert">请输入 0 到 999999 之间的整数，或留空。</p>}
          {folderSortDuplicateCount > 0 && <p className="rounded-ui-md border border-warning/40 bg-warning/10 px-3 py-2 text-ui-sm" role="status">当前目录已有 {folderSortDuplicateCount} 个文件夹使用序号 {parsedFolderSortOrder}，保存后将按名称继续排序。</p>}
          {folderActionError && <p className="text-ui-sm text-destructive" role="alert">{folderActionError}</p>}
        </div>}
        <DialogFooter><Button variant="outline" onClick={() => setFolderSortTarget(null)} disabled={busyAction === `folder:${folderSortTarget?.id}:sort`}>取消</Button><Button onClick={() => void saveFolderSort()} disabled={!folderSortValid || busyAction === `folder:${folderSortTarget?.id}:sort`}>{busyAction === `folder:${folderSortTarget?.id}:sort` ? "保存中…" : "保存顺序"}</Button></DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog open={Boolean(folderRenameTarget)} onOpenChange={(open) => {
      if (!open && !busyAction?.startsWith("folder:")) {
        setFolderRenameTarget(null);
        setFolderActionError(null);
      }
    }}>
      <DialogContent>
        <DialogHeader><DialogTitle>重命名文件夹</DialogTitle><DialogDescription>只修改文件夹名称；显示编号、资料归属和发布状态保持不变。</DialogDescription></DialogHeader>
        {folderRenameTarget && <div className="space-y-4">
          <div className="rounded-ui-md border border-border bg-surface-muted/40 px-3 py-2 text-ui-sm"><p className="text-ui-xs text-muted-foreground">当前路径</p><p className="mt-1 break-words font-medium">{folderRenameTarget.full_path}</p></div>
          <label className="block space-y-1.5 text-ui-sm font-medium"><span>文件夹名称</span><Input value={folderRenameName} maxLength={100} onChange={(event) => { setFolderRenameName(event.target.value); setFolderActionError(null); }} aria-label="文件夹名称" autoFocus /><span className="block text-right text-ui-xs font-normal text-muted-foreground">{folderRenameName.trim().length}/100</span></label>
          {folderRenameConflict && <p className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui-sm text-destructive" role="alert">当前目录已有同名文件夹“{folderRenameConflict.display_name}”，请使用其他名称。</p>}
          {folderActionError && <p className="text-ui-sm text-destructive" role="alert">{folderActionError}</p>}
        </div>}
        <DialogFooter><Button variant="outline" onClick={() => setFolderRenameTarget(null)} disabled={busyAction === `folder:${folderRenameTarget?.id}:rename`}>取消</Button><Button onClick={() => void saveFolderRename()} disabled={!folderRenameName.trim() || Boolean(folderRenameConflict) || normalizeFolderName(folderRenameName) === normalizeFolderName(folderRenameTarget?.display_name || "") || busyAction === `folder:${folderRenameTarget?.id}:rename`}>{busyAction === `folder:${folderRenameTarget?.id}:rename` ? "保存中…" : "保存名称"}</Button></DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog open={Boolean(folderMoveTarget)} onOpenChange={(open) => {
      if (!open && !busyAction?.startsWith("folder:")) {
        setFolderMoveTarget(null);
        setFolderMoveParentId("");
        setFolderActionError(null);
      }
    }}>
      <DialogContent className="max-h-[calc(100vh-2rem)] max-w-2xl overflow-y-auto">
        <DialogHeader><DialogTitle>移动文件夹位置</DialogTitle><DialogDescription>移动会更新完整路径，但不会改变资料归属、发布状态或稳定分类标识。</DialogDescription></DialogHeader>
        {folderMoveTarget && <div className="space-y-4">
          <div className="rounded-ui-md border border-border bg-surface-muted/40 px-3 py-2 text-ui-sm"><p className="text-ui-xs text-muted-foreground">当前路径</p><p className="mt-1 break-words font-medium">{folderMoveTarget.full_path}</p></div>
          <CategoryTreePicker categories={categories} value={folderMoveParentId} onChange={(value) => { setFolderMoveParentId(value); setFolderActionError(null); }} currentCategoryId={folderMoveTarget.parent_id} label="目标位置" rootOption={{ value: ROOT_FOLDER_VALUE, label: "根目录 /", disabledReason: folderMoveConstraints.rootReason }} disabledCategoryReasons={folderMoveConstraints.reasons} disabled={busyAction === `folder:${folderMoveTarget.id}:move`} />
          {folderMoveParentId && <p className="break-words text-ui-xs text-muted-foreground">新路径：{folderMoveParentId === ROOT_FOLDER_VALUE ? "/" : categories.find((category) => category.id === folderMoveParentId)?.full_path} / {folderMoveTarget.display_code} {folderMoveTarget.display_name}</p>}
          {folderActionError && <p className="text-ui-sm text-destructive" role="alert">{folderActionError}</p>}
        </div>}
        <DialogFooter><Button variant="outline" onClick={() => setFolderMoveTarget(null)} disabled={busyAction === `folder:${folderMoveTarget?.id}:move`}>取消</Button><Button onClick={() => void saveFolderMove()} disabled={!folderMoveParentId || Boolean(folderMoveParentId === ROOT_FOLDER_VALUE ? folderMoveConstraints.rootReason : folderMoveConstraints.reasons[folderMoveParentId]) || busyAction === `folder:${folderMoveTarget?.id}:move`}><FolderInput className="size-4" />{busyAction === `folder:${folderMoveTarget?.id}:move` ? "移动中…" : "确认移动"}</Button></DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog open={Boolean(bulkAction && bulkAction !== "archive")} onOpenChange={(open) => {
      if (!open && busyAction !== "bulk") {
        setBulkAction(null);
        setBulkFailures([]);
        setBulkMoveFolderId("");
        setBulkNote("");
      }
    }}>
      <DialogContent className={bulkAction === "move" ? "max-h-[calc(100vh-2rem)] max-w-2xl overflow-y-auto" : undefined}>
        <DialogHeader>
          <DialogTitle>{bulkAction === "move" ? bulkMoveLabel : bulkAction === "publish" ? "批量发布资料" : bulkAction === "reject" ? "批量退回资料" : "批量确认资料"}</DialogTitle>
          <DialogDescription>已选择 {selectedItems.length} 份资料。系统会逐项执行，并保留不符合状态或权限要求的失败原因。</DialogDescription>
        </DialogHeader>
        {bulkAction === "move" && <CategoryTreePicker categories={categories} value={bulkMoveFolderId} onChange={setBulkMoveFolderId} label="目标目录" />}
        {bulkAction === "reject" && <label className="block space-y-1.5 text-ui-sm font-medium">
          <span>退回原因</span>
          <textarea aria-label="批量退回原因" value={bulkNote} onChange={(event) => setBulkNote(event.target.value)} maxLength={2000} className="min-h-28 w-full resize-y rounded-ui-md border border-input bg-background px-3 py-2 text-ui-sm" placeholder="请说明需要修改的内容" />
          <span className="block text-right text-ui-xs font-normal text-muted-foreground">{bulkNote.length}/2000</span>
        </label>}
        {bulkFailures.length > 0 && <div className="space-y-2 text-ui-sm text-destructive" role="alert"><p>上次操作有 {bulkFailures.length} 份失败：</p><ul className="max-h-48 space-y-1 overflow-y-auto border-y border-destructive/30 py-2">{bulkFailures.map((entry) => <li key={entry.version_id} className="break-words"><span className="font-medium">{entry.title}</span>{entry.message ? `：${entry.message}` : "：请刷新后重试"}</li>)}</ul></div>}
        <DialogFooter>
          <Button variant="outline" onClick={() => setBulkAction(null)} disabled={busyAction === "bulk"}>取消</Button>
          <Button onClick={() => void executeBulk()} disabled={busyAction === "bulk" || selectedItems.length === 0 || (bulkAction === "move" && !bulkMoveFolderId) || (bulkAction === "reject" && !bulkNote.trim())}>{busyAction === "bulk" ? "处理中…" : bulkFailures.length ? "重试失败项" : "确认执行"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog open={Boolean(detail) && !previewState.parentId} onOpenChange={(open) => { if (!open && !previewState.parentId) setDetail(null); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{detail?.title || "资料详情"}</DialogTitle>
          <DialogDescription>{detail?.content_kind === "media_transcript" ? "查看已发布的视频转录稿；校对、发布和删除仍在视频管理中完成。" : "核对文件、分类、来源、版本和最近审核记录。"}</DialogDescription>
        </DialogHeader>
        {detail && <div className="space-y-4">
          <PublicationFailure item={detail} />
          <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-2 text-ui-sm [&_dt]:whitespace-nowrap">
            <dt className="text-muted-foreground">类型</dt><dd>{detail.content_kind === "media_transcript" ? "视频转录稿" : "文档"}</dd>
            <dt className="text-muted-foreground">文件名</dt><dd className="break-all">{detail.original_filename}</dd>
            <dt className="text-muted-foreground">分类</dt><dd className="break-words">{detail.category_path || detail.category_label}</dd>
            <dt className="text-muted-foreground">状态</dt><dd><Badge variant={statusVariant(detail.lifecycle_status)}>{statusLabel[detail.lifecycle_status]}</Badge></dd>
            <dt className="text-muted-foreground">来源</dt><dd>{sourceLabel[detail.source_origin] || "其他来源"}</dd>
            <dt className="text-muted-foreground">版本</dt><dd>v{detail.version_number}</dd>
            {detail.content_kind === "media_transcript" && <>
              <dt className="text-muted-foreground">视频时长</dt><dd>{formatMediaDuration(detail.media_duration_ms) || "未记录"}</dd>
              <dt className="text-muted-foreground">视频大小</dt><dd>{detail.media_file_size != null ? formatUploadSize(detail.media_file_size) : "未记录"}</dd>
              <dt className="text-muted-foreground">后续版本</dt><dd>{detail.has_pending_revision ? <Badge variant="warning">有新转录稿待处理</Badge> : "无待处理稿"}</dd>
            </>}
            <dt className="text-muted-foreground">创建时间</dt><dd>{formatAdminDate(detail.created_at)}</dd>
            <dt className="text-muted-foreground">最后更新时间</dt><dd className="whitespace-nowrap">{formatAdminDate(detail.updated_at)}</dd>
            <dt className="text-muted-foreground">发布尝试</dt><dd>共 {detail.publication_attempt_count} 次</dd>
            {detail.latest_review_decision && <>
              <dt className="text-muted-foreground">最近审核人</dt><dd>{detail.latest_reviewed_by_name || "未知"}</dd>
              <dt className="text-muted-foreground">审核时间</dt><dd>{detail.latest_reviewed_at ? formatAdminDate(detail.latest_reviewed_at) : "未知"}</dd>
              <dt className="text-muted-foreground">审核结果</dt><dd>{detail.latest_review_decision === "approved" ? "确认通过" : "退回修改"}</dd>
              <dt className="text-muted-foreground">{detail.latest_review_decision === "rejected" ? "退回原因" : "审核备注"}</dt><dd className="break-words">{detail.latest_review_note || "未填写"}</dd>
            </>}
          </dl>
          {detail.content_kind === "media_transcript" ? <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => { setDetail(null); openVideoPreview({ mediaId: detail.media_id!, title: detail.title, startSeconds: 0, fromSource: false }); }} disabled={!detail.media_id}><Film className="size-4" />播放视频与转录稿</Button>
            <a className={buttonVariants({ variant: "outline" })} href={`/admin/media?media_id=${encodeURIComponent(detail.media_id || "")}&workbench=1`}><ExternalLink className="size-4" />进入视频管理</a>
          </div> : <div className="flex flex-wrap gap-2">
            {detail.preview_parent_id && ["pdf", "docx", "xlsx", "pptx"].includes(detail.doc_type) ? <Button variant="outline" onClick={() => { openDocumentPreview(detail.preview_parent_id!, detail.title, detail.doc_type, 1, {}, "managed-content-detail"); }}><Eye className="size-4" />预览文件</Button> : detail.doc_type === "pdf" || can("item.download") ? <a className={buttonVariants({ variant: "outline" })} href={adminContentApi.fileUrl(detail.version_id)} target="_blank" rel="noreferrer"><Eye className="size-4" />打开文件</a> : <Button variant="outline" disabled title="打开文件（需要下载权限）"><Eye className="size-4" />打开文件</Button>}
            {can("item.submit") && ["draft", "rejected"].includes(detail.lifecycle_status) && <Button onClick={() => void act(detail, "submit", () => adminContentApi.submit(detail.version_id), "已提交确认")} disabled={Boolean(busyAction)}><Send className="size-4" />{busyAction === `${detail.version_id}:submit` ? "提交中…" : detail.lifecycle_status === "rejected" ? "重新提交" : "提交"}</Button>}
            {can("item.review") && detail.lifecycle_status === "awaiting_review" && <Button onClick={() => openReviewDialog(detail)} disabled={Boolean(busyAction)}><Check className="size-4" />审核</Button>}
            {can("item.publish") && ["approved", "publication_failed"].includes(detail.lifecycle_status) && <Button onClick={() => openPublishDialog(detail)} disabled={Boolean(busyAction)}><Rocket className="size-4" />{detail.lifecycle_status === "publication_failed" ? "重新发布" : "发布"}</Button>}
          </div>}
        </div>}
      </DialogContent>
    </Dialog>

    <Dialog open={Boolean(reviewTarget) && !previewState.parentId} onOpenChange={(open) => {
      if (!open && !previewState.parentId && busyAction !== "review") {
        setReviewTarget(null);
        setReviewError(null);
      }
    }}>
      <DialogContent className="max-h-[calc(100vh-2rem)] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>审核资料</DialogTitle>
          <DialogDescription>确认资料信息并记录本次审核结果。</DialogDescription>
        </DialogHeader>
        {reviewTarget && <div className="space-y-4">
          <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-2 text-ui-sm [&_dt]:whitespace-nowrap">
            <dt className="text-muted-foreground">资料</dt><dd className="break-words font-medium">{reviewTarget.title}</dd>
            <dt className="text-muted-foreground">目录</dt><dd className="break-words">{reviewTarget.category_path || reviewTarget.category_label}</dd>
            <dt className="text-muted-foreground">文件</dt><dd className="break-all">{reviewTarget.original_filename}</dd>
            <dt className="text-muted-foreground">版本</dt><dd>v{reviewTarget.version_number}</dd>
            <dt className="text-muted-foreground">来源</dt><dd>{sourceLabel[reviewTarget.source_origin] || "其他来源"}</dd>
          </dl>
          <div>
            {reviewTarget.preview_parent_id && ["pdf", "docx", "xlsx", "pptx"].includes(reviewTarget.doc_type) ? <Button variant="outline" onClick={() => openDocumentPreview(reviewTarget.preview_parent_id!, reviewTarget.title, reviewTarget.doc_type, 1, {}, "managed-content-review")}><Eye className="size-4" />预览文件</Button> : reviewTarget.doc_type === "pdf" || can("item.download") ? <a className={buttonVariants({ variant: "outline" })} href={adminContentApi.fileUrl(reviewTarget.version_id)} target="_blank" rel="noreferrer"><Eye className="size-4" />打开文件</a> : <Button variant="outline" disabled title="打开文件（需要下载权限）"><Eye className="size-4" />打开文件</Button>}
          </div>
          <div className="grid grid-cols-2 gap-2" role="group" aria-label="审核结果">
            <Button type="button" aria-label="选择确认通过" variant={reviewDecision === "approve" ? "default" : "outline"} aria-pressed={reviewDecision === "approve"} disabled={busyAction === "review"} onClick={() => { setReviewDecision("approve"); setReviewError(null); }}><Check className="size-4" />确认通过</Button>
            <Button type="button" aria-label="选择退回修改" variant={reviewDecision === "reject" ? "destructive" : "outline"} aria-pressed={reviewDecision === "reject"} disabled={busyAction === "review"} onClick={() => { setReviewDecision("reject"); setReviewError(null); }}><X className="size-4" />退回修改</Button>
          </div>
          <label className="block space-y-1.5 text-ui-sm font-medium">
            <span>{reviewDecision === "approve" ? "审核备注（可选）" : "退回原因"}</span>
            <textarea aria-label={reviewDecision === "approve" ? "审核备注（可选）" : "退回原因"} value={reviewNote} onChange={(event) => { setReviewNote(event.target.value); setReviewError(null); }} maxLength={2000} className="min-h-28 w-full resize-y rounded-ui-md border border-input bg-background px-3 py-2 text-ui-sm" placeholder={reviewDecision === "approve" ? "可记录审核依据" : "请说明需要修改的内容"} autoFocus />
            <span className="block text-right text-ui-xs font-normal text-muted-foreground">{reviewNote.length}/2000</span>
          </label>
          {reviewError && <p className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui-sm text-destructive" role="alert">{reviewError}</p>}
        </div>}
        <DialogFooter>
          <Button variant="outline" disabled={busyAction === "review"} onClick={() => setReviewTarget(null)}>取消</Button>
          <Button variant={reviewDecision === "reject" ? "destructive" : "default"} disabled={busyAction === "review" || (reviewDecision === "reject" && !reviewNote.trim())} onClick={() => void submitReview()}>{busyAction === "review" ? "提交中…" : reviewDecision === "approve" ? "确认通过" : "确认退回"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog open={Boolean(publishTarget)} onOpenChange={(open) => {
      if (!open && busyAction !== "publish") {
        setPublishTarget(null);
        setPublishError(null);
      }
    }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{publishTarget?.lifecycle_status === "publication_failed" ? "重新发布资料" : "发布资料"}</DialogTitle>
          <DialogDescription>发布后系统会创建索引任务，完成后资料才会进入知识库检索。</DialogDescription>
        </DialogHeader>
        {publishTarget && <div className="space-y-3 text-ui-sm">
          <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-2">
            <dt className="text-muted-foreground">资料</dt><dd className="break-words font-medium">{publishTarget.title}</dd>
            <dt className="text-muted-foreground">目录</dt><dd className="break-words">{publishTarget.category_path || publishTarget.category_label}</dd>
            <dt className="text-muted-foreground">版本</dt><dd>v{publishTarget.version_number}</dd>
          </dl>
          <PublicationFailure item={publishTarget} />
          {publishError && <p className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-destructive" role="alert">{publishError}</p>}
        </div>}
        <DialogFooter>
          <Button variant="outline" disabled={busyAction === "publish"} onClick={() => setPublishTarget(null)}>取消</Button>
          <Button disabled={busyAction === "publish"} onClick={() => void publishContent()}>{busyAction === "publish" ? "发布中…" : publishTarget?.lifecycle_status === "publication_failed" ? "确认重新发布" : "确认发布"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog open={requestFolderOpen} onOpenChange={setRequestFolderOpen}><DialogContent><DialogHeader><DialogTitle>申请新建文件夹</DialogTitle><DialogDescription>申请将在“{currentFolder?.display_name || "当前目录"}”下创建受控目录，由资料负责人审批。</DialogDescription></DialogHeader><label className="space-y-1.5 text-ui-sm font-medium"><span>文件夹名称</span><Input value={requestFolderName} onChange={(event) => setRequestFolderName(event.target.value)} placeholder="例如：净高分析" autoFocus /></label><DialogFooter><Button variant="outline" onClick={() => setRequestFolderOpen(false)} disabled={busyAction === "request-folder"}>取消</Button><Button onClick={() => void requestFolder()} disabled={!requestFolderName.trim() || busyAction === "request-folder"}>{busyAction === "request-folder" ? "提交中…" : "提交申请"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={newFolderOpen} onOpenChange={setNewFolderOpen}><DialogContent><DialogHeader><DialogTitle>新建文件夹</DialogTitle><DialogDescription>文件夹将建立在“{currentFolder?.display_name || "当前目录"}”下，最多支持四级目录。</DialogDescription></DialogHeader><label className="space-y-1.5 text-ui-sm font-medium"><span>文件夹名称</span><Input value={newFolderName} onChange={(event) => setNewFolderName(event.target.value)} placeholder="例如：净高分析" autoFocus /></label><DialogFooter><Button variant="outline" onClick={() => setNewFolderOpen(false)} disabled={busyAction === "new-folder"}>取消</Button><Button onClick={() => void createFolder()} disabled={!newFolderName.trim() || busyAction === "new-folder"}>{busyAction === "new-folder" ? "创建中…" : "创建"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={Boolean(moveTarget)} onOpenChange={(open) => { if (!open && !busyAction?.endsWith(":move")) { setMoveTarget(null); setMoveFolderId(""); setMoveError(null); } }}><DialogContent className="max-w-2xl max-h-[calc(100vh-2rem)] overflow-y-auto"><DialogHeader><DialogTitle>{moveTarget?.content_kind === "media_transcript" ? "调整归档目录" : moveTarget?.has_published_head ? "调整分类" : "移动资料"}</DialogTitle><DialogDescription>{moveTarget?.content_kind === "media_transcript" ? `只调整“${moveTarget.title}”在资料库中的归档目录，不改变视频、转录发布状态或索引。` : moveTarget?.has_published_head ? `调整“${moveTarget.title}”的正式分类。同步完成前资料仍保留在原目录并继续正常检索。` : `将“${moveTarget?.title || "资料"}”从当前目录移动到另一个受控目录。`}</DialogDescription></DialogHeader>{moveTarget && <CategoryTreePicker categories={categories} value={moveFolderId} currentCategoryId={moveTarget.category_id} onChange={(categoryId) => { setMoveFolderId(categoryId); setMoveError(null); }} label="目标目录" />}{moveError && <p className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui-sm text-destructive" role="alert">{moveError}</p>}<DialogFooter><Button variant="outline" onClick={() => { setMoveTarget(null); setMoveFolderId(""); setMoveError(null); }} disabled={Boolean(busyAction?.endsWith(":move"))}>取消</Button><Button onClick={() => void moveContent()} disabled={!moveFolderId || moveFolderId === moveTarget?.category_id || Boolean(busyAction?.endsWith(":move"))}>{busyAction?.endsWith(":move") ? "处理中…" : moveTarget?.content_kind === "media_transcript" ? "确认调整" : moveTarget?.has_published_head ? "提交分类调整" : "确认移动"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={Boolean(renameTarget)} onOpenChange={(open) => { if (!open && busyAction !== "rename") { setRenameTarget(null); setRenameConflict(null); setRenameError(null); } }}><DialogContent><DialogHeader><DialogTitle>重命名资料</DialogTitle><DialogDescription>标题和源文件名会作为新草稿版本保存，之后需要重新确认并发布。</DialogDescription></DialogHeader><div className="space-y-3"><label className="block space-y-1.5 text-ui-sm font-medium"><span>资料标题</span><Input value={renameTitle} onChange={(event) => { setRenameTitle(event.target.value); setRenameConflict(null); }} /></label><label className="block space-y-1.5 text-ui-sm font-medium"><span>源文件名</span><Input value={renameFilename} onChange={(event) => { setRenameFilename(event.target.value); setRenameConflict(null); }} /><span className="block text-ui-xs font-normal text-muted-foreground">只能修改名称，不能改变文件扩展名。</span></label>{renameConflict && <div className="space-y-2 rounded-ui-md border border-warning/50 bg-warning/10 p-3 text-ui-sm" role="alert"><p className="font-medium">当前目录存在同名资料，是否替换？</p><p className="break-words">{renameConflict.title}（{renameConflict.original_filename}）</p><p className="text-muted-foreground">替换会将上述资料移入回收站并立即停止检索；当前资料的新版本仍需重新确认和发布。</p></div>}{renameError && <p className="text-ui-sm text-destructive" role="alert">{renameError}</p>}</div><DialogFooter><Button variant="outline" disabled={busyAction === "rename"} onClick={() => setRenameTarget(null)}>取消</Button>{renameConflict ? <Button variant="destructive" disabled={busyAction === "rename"} onClick={() => void renameContent(true)}>{busyAction === "rename" ? "替换中…" : "确认替换并重命名"}</Button> : <Button disabled={busyAction === "rename" || !renameTitle.trim() || !renameFilename.trim()} onClick={() => void renameContent()}>{busyAction === "rename" ? "保存中…" : "保存为新版本"}</Button>}</DialogFooter></DialogContent></Dialog>

    <Dialog open={Boolean(updateTarget)} onOpenChange={(open) => { if (!open && busyAction !== "update") { setUpdateTarget(null); setUpdateConflict(null); setUpdateError(null); setUpdateFile(null); } }}><DialogContent><DialogHeader><DialogTitle>更新资料文件</DialogTitle><DialogDescription>上传替换文件后会创建新草稿版本，旧发布版本会继续检索，直到新版本发布成功。</DialogDescription></DialogHeader><div className="space-y-3"><label className="flex min-h-32 cursor-pointer flex-col items-center justify-center gap-2 rounded-ui-lg border border-dashed border-input bg-background px-4 py-5 text-center hover:bg-surface-muted focus-within:ring-2 focus-within:ring-ring"><FileUp className="size-6 text-primary" /><span className="text-ui-sm font-medium">{updateFile ? updateFile.name : "选择替换文件"}</span><span className="text-ui-xs text-muted-foreground">支持 PDF、Markdown、Word、Excel 和 PPT</span><input ref={updateFileInputRef} type="file" className="sr-only" aria-label="选择替换文件" accept=".pdf,.md,.docx,.xlsx,.pptx" onChange={(event) => { setUpdateFile(event.target.files?.[0] || null); setUpdateConflict(null); }} /></label>{updateFile && <div className="space-y-2"><p className="text-ui-sm font-medium">文件名处理</p><div className="grid grid-cols-2 gap-2" role="group" aria-label="文件名处理"><Button type="button" variant={updateFilenameMode === "old" ? "default" : "outline"} aria-pressed={updateFilenameMode === "old"} onClick={() => { setUpdateFilenameMode("old"); setUpdateConflict(null); }}>沿用原名称</Button><Button type="button" variant={updateFilenameMode === "new" ? "default" : "outline"} aria-pressed={updateFilenameMode === "new"} onClick={() => { setUpdateFilenameMode("new"); setUpdateConflict(null); }}>使用新文件名</Button></div><p className="break-all text-ui-xs text-muted-foreground">{updateFilenameMode === "old" ? `将使用原名称并匹配新格式：${filenameForOldMode(updateTarget?.original_filename || "", updateFile.name)}` : `将使用：${updateFile.name}`}</p></div>}{updateConflict && <div className="space-y-2 rounded-ui-md border border-warning/50 bg-warning/10 p-3 text-ui-sm" role="alert"><p className="font-medium">当前目录存在同名资料，是否替换？</p><p>{updateConflict.title}（{updateConflict.original_filename}）</p><p className="text-muted-foreground">替换会将上述资料移入回收站并停止检索。</p></div>}{updateError && <p className="text-ui-sm text-destructive" role="alert">{updateError}</p>}</div><DialogFooter><Button variant="outline" disabled={busyAction === "update"} onClick={() => setUpdateTarget(null)}>取消</Button>{updateConflict ? <Button variant="destructive" disabled={busyAction === "update"} onClick={() => void updateContent(true)}>{busyAction === "update" ? "替换中…" : "确认替换并更新"}</Button> : <Button disabled={!updateFile || busyAction === "update"} onClick={() => void updateContent()}>{busyAction === "update" ? "上传中…" : "确认更新"}</Button>}</DialogFooter></DialogContent></Dialog>

    <Dialog open={uploadDialogOpen} onOpenChange={(open) => { if (!open) closeUploadDialog(); }}><DialogContent><DialogHeader><DialogTitle>上传文件</DialogTitle><DialogDescription>文件将上传到当前目录“{currentFolder?.full_path || "请选择目录"}”，上传后先进入待提交状态。</DialogDescription></DialogHeader><label onDragEnter={(event) => { event.preventDefault(); setDragActive(true); }} onDragOver={(event) => event.preventDefault()} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragActive(false); }} onDrop={(event) => { event.preventDefault(); setDragActive(false); void inspectDroppedUpload(event.dataTransfer, "select"); }} className={`flex min-h-36 cursor-pointer flex-col items-center justify-center gap-2 rounded-ui-lg border border-dashed px-4 py-6 text-center transition-colors duration-normal focus-within:ring-2 focus-within:ring-ring ${dragActive ? "border-primary bg-primary/5" : "border-input bg-background hover:bg-surface-muted"}`}><Upload className="size-6 text-primary" /><span className="text-ui-sm font-medium">{folderScanning ? "正在读取文件夹…" : "拖动文件到这里，或选择文件"}</span><span className="text-ui-xs text-muted-foreground">支持 PDF、Markdown、Word、Excel 和 PPT</span><input ref={fileInputRef} aria-label="选择资料文件" type="file" multiple accept=".pdf,.md,.docx,.xlsx,.pptx" className="sr-only" disabled={uploading || folderScanning} onChange={(event) => acceptFiles(Array.from(event.target.files || []))} /></label><Button type="button" variant="outline" className="w-full" onClick={() => folderInputRef.current?.click()} disabled={uploading || folderScanning}><Folder className="size-4" />上传文件夹</Button><input ref={folderInputRef} aria-label="选择资料文件夹" type="file" multiple className="sr-only" disabled={uploading || folderScanning} onChange={(event) => selectFolder(Array.from(event.target.files || []))} {...({ webkitdirectory: "", directory: "" } as React.InputHTMLAttributes<HTMLInputElement>)} />{files.length > 0 && <ul className="max-h-40 space-y-1 overflow-y-auto rounded-ui-md border border-border px-3 py-2 text-ui-sm">{files.map((file) => <li key={`${file.name}-${file.size}`} className="break-all">{file.name}<span className="ml-2 text-ui-xs text-muted-foreground">{formatUploadSize(file.size)}</span></li>)}</ul>}<DialogFooter><Button variant="outline" onClick={closeUploadDialog} disabled={uploading || folderScanning}>取消</Button><Button onClick={() => void confirmDialogUpload()} disabled={!files.length || uploading || folderScanning || !currentFolderId}>{uploading ? "上传中…" : "确定上传"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={pendingUploadFiles.length > 0} onOpenChange={(open) => { if (!open && !uploading) { setPendingUploadFiles([]); setPendingUploadFolderId(""); } }}><DialogContent><DialogHeader><DialogTitle>确认上传</DialogTitle><DialogDescription>将上传到“{categories.find((category) => category.id === pendingUploadFolderId)?.full_path || currentFolder?.full_path || "当前目录"}”，确认后文件会进入待提交状态。</DialogDescription></DialogHeader><div className="space-y-2 text-ui-sm"><p>共 {pendingUploadFiles.length} 个文件</p><ul className="max-h-48 space-y-1 overflow-y-auto rounded-ui-md border border-border px-3 py-2">{pendingUploadFiles.map((file) => <li key={`${file.name}-${file.size}`} className="break-all">{file.name}<span className="ml-2 text-ui-xs text-muted-foreground">{formatUploadSize(file.size)}</span></li>)}</ul></div><DialogFooter><Button variant="outline" onClick={() => { setPendingUploadFiles([]); setPendingUploadFolderId(""); }} disabled={uploading}>取消</Button><Button onClick={() => void confirmFileDropUpload()} disabled={uploading}>{uploading ? "上传中…" : "确定上传"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={Boolean(pendingFolderUpload)} onOpenChange={(open) => { if (!open && !uploading) { setPendingFolderUpload(null); setPendingFolderUploadFolderId(""); } }}><DialogContent className="max-w-2xl"><DialogHeader><DialogTitle>上传文件夹</DialogTitle><DialogDescription>确认后将按相对路径上传到“{categories.find((category) => category.id === pendingFolderUploadFolderId)?.full_path || currentFolder?.full_path || "当前目录"}”。缺少的目录仍按当前账号权限创建，文件上传后进入待提交状态。</DialogDescription></DialogHeader>{pendingFolderUpload && <div className="space-y-4 text-ui-sm"><dl className="grid grid-cols-2 gap-2 rounded-ui-md border border-border bg-surface-muted/40 p-3 sm:grid-cols-4"><div className="col-span-2 sm:col-span-4"><dt className="text-ui-xs text-muted-foreground">根文件夹</dt><dd className="mt-1 break-all font-medium">{pendingFolderUpload.rootFolderNames.length > 1 ? `${pendingFolderUpload.rootFolderNames[0]} 等 ${pendingFolderUpload.rootFolderNames.length} 个根文件夹` : pendingFolderUpload.rootFolderNames[0] || "所选文件夹"}</dd></div><div><dt className="text-ui-xs text-muted-foreground">文件夹</dt><dd className="mt-1 font-medium tabular-nums">{pendingFolderUpload.folderCount} 个</dd></div><div><dt className="text-ui-xs text-muted-foreground">可上传文件</dt><dd className="mt-1 font-medium tabular-nums">{pendingFolderUpload.fileCount} 个</dd></div><div><dt className="text-ui-xs text-muted-foreground">已忽略</dt><dd className="mt-1 font-medium tabular-nums">{pendingFolderUpload.ignoredEntries.length} 个</dd></div><div><dt className="text-ui-xs text-muted-foreground">上传大小</dt><dd className="mt-1 font-medium tabular-nums">{formatUploadSize(pendingFolderUpload.totalSize)}</dd></div></dl>{pendingFolderUpload.ignoredEntries.length > 0 && <div className="space-y-1"><p className="text-ui-xs font-medium text-muted-foreground">以下格式不受支持，将被忽略</p><ul className="max-h-24 space-y-1 overflow-y-auto rounded-ui-md border border-warning/40 bg-warning/10 px-3 py-2 text-ui-xs">{pendingFolderUpload.ignoredEntries.map((entry) => <li key={entry.relativePath} className="break-all">{entry.relativePath}</li>)}</ul></div>}<div className="space-y-1"><p className="text-ui-xs font-medium text-muted-foreground">将上传的文件</p><ul className="max-h-40 space-y-1 overflow-y-auto rounded-ui-md border border-border px-3 py-2">{pendingFolderUpload.entries.map((entry) => <li key={entry.relativePath} className="flex items-start justify-between gap-3"><span className="min-w-0 break-all">{entry.relativePath}</span><span className="shrink-0 text-ui-xs text-muted-foreground">{formatUploadSize(entry.file.size)}</span></li>)}</ul></div></div>}<DialogFooter><Button variant="outline" onClick={() => { setPendingFolderUpload(null); setPendingFolderUploadFolderId(""); }} disabled={uploading}>取消</Button><Button onClick={() => void confirmFolderUpload()} disabled={uploading || !pendingFolderUpload?.fileCount}>{uploading ? "上传中…" : "开始上传"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={deleteTargets.length > 0} onOpenChange={(open) => { if (!open && busyAction !== "archive") { setDeleteTargets([]); setDeleteAcknowledged(false); setDeleteError(null); } }}><DialogContent><DialogHeader><DialogTitle>{deleteTargets.length > 1 ? `将 ${deleteTargets.length} 份资料移入回收站？` : "将资料移入回收站？"}</DialogTitle><DialogDescription>以下资料将立即停止进入知识库检索。文件、版本及审核发布历史会保留，可从回收站恢复。</DialogDescription></DialogHeader><ul className="max-h-48 space-y-2 overflow-y-auto rounded-ui-md border border-border p-3 text-ui-sm">{deleteTargets.map((item) => <li key={item.item_id} className="min-w-0"><p className="break-words font-medium">{item.title}</p><p className="break-all text-ui-xs text-muted-foreground">{item.original_filename}</p></li>)}</ul><label className="flex items-start gap-2 rounded-ui-md border border-destructive/30 bg-destructive/5 p-3 text-ui-sm"><Checkbox className="mt-0.5" checked={deleteAcknowledged} onChange={(event) => setDeleteAcknowledged(event.target.checked)} /><span>我已了解这些资料移入回收站后将不再进入检索。</span></label>{deleteError && <p className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui-sm text-destructive" role="alert">{deleteError}</p>}<DialogFooter><Button variant="outline" disabled={busyAction === "archive"} onClick={() => setDeleteTargets([])}>取消</Button><Button variant="destructive" disabled={busyAction === "archive" || !deleteAcknowledged} onClick={() => void deleteContent()}>{busyAction === "archive" ? "处理中…" : "确认移入回收站"}</Button></DialogFooter></DialogContent></Dialog>
  </section>;
}
