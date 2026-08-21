import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle,
  Archive,
  ArchiveRestore,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Download,
  ExternalLink,
  Eye,
  FilePenLine,
  FileText,
  FileUp,
  Film,
  Folder,
  FolderInput,
  FolderPlus,
  History,
  Info,
  ListChecks,
  ListOrdered,
  MoreHorizontal,
  Pencil,
  RefreshCw,
  RotateCcw,
  Rocket,
  Search,
  Send,
  SlidersHorizontal,
  Trash2,
  Upload,
  Video,
  X,
  XCircle,
} from "lucide-react";
import { adminContentApi } from "../../api/admin/content";
import { Badge } from "../../components/ui/badge";
import { CategoryTreePicker } from "../../components/admin/CategoryTreePicker";
import { CategoryCascader } from "../../components/admin/CategoryCascader";
import { Button, buttonVariants } from "../../components/ui/button";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
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
import { IconButton } from "../../components/ui/icon-button";
import { LoadingState } from "../../components/ui/loading-state";
import { Select } from "../../components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "../../components/ui/sheet";
import { toast } from "../../components/ui/toast";
import { useAuth } from "../../context/AuthContext";
import { usePdfPreview } from "../../hooks/usePdfPreview";
import { useVideoPlayer } from "../../hooks/useVideoPlayer";
import type {
  BulkManagedContentResult,
  BulkOperationAction,
  BulkRestorePreflightResult,
  ContentPermission,
  ContentTrashAuditEvent,
  FolderRequest,
  ManagedCategory,
  ManagedContentItem,
  ManagedUploadConflictAction,
  ManagedUploadPreflightResponse,
  ManagedUploadTask,
  ManagedUploadTaskEntry,
  TranscriptionSchemeOption,
  TrashPurgePreflight,
  TrashPurgeRun,
  TrashSettings,
} from "../../types";
import type {
  ManagedUploadOptions,
  ManagedUploadProgress,
} from "../../api/client";
import { formatAdminDate } from "../../lib/admin-formatters";
import { createRequestId } from "../../lib/request-id";
import { AdminDocumentsPage } from "./AdminDocumentsPage";
import { ManagedSummaryCard } from "../../components/admin/ManagedSummaryCard";
import { CategoryDeleteDialog } from "../../components/admin/CategoryDeleteDialog";
import { CategoryDestinationPicker } from "../../components/admin/CategoryDestinationPicker";
import { ManagedContentBulkOperationDialog } from "../../components/admin/ManagedContentBulkOperationDialog";
import { ManagedItemType } from "../../components/admin/ManagedItemType";
import { compareManagedCategories } from "../../lib/category-tree";
import { AdminTranscriptionTasksPage } from "./AdminTranscriptionTasksPage";
import { useManagedContentLiveRefresh } from "../../hooks/useManagedContentLiveRefresh";
import {
  collectDroppedUpload,
  folderSelectionFromFiles,
  type FolderUploadEntry,
  type FolderUploadSelection,
} from "../../lib/folder-upload";

const PAGE_SIZE = 25;
const PAGE_SIZE_OPTIONS = [25, 50, 100];
const UPLOAD_TASK_PAGE_SIZE_OPTIONS = [10, 25, 50];
const BULK_DOWNLOAD_TOAST_ID = "managed-content-bulk-download";
const ACTIVE_RECLASSIFICATION_STATUSES = new Set([
  "pending",
  "applying",
  "committing",
  "rolling_back",
]);
type SortKey = "docType" | "title" | "updatedAt" | "status" | "source";
type SortDirection = "asc" | "desc";
type TrashSortKey = "title" | "category" | "status" | "source" | "retention" | "archivedAt";
type ManagedContentView =
  "library" | "trash" | "uploads" | "index" | "transcription";
type MoveOperation = "move" | "reclassify" | "archive";
type UploadConflictChoice = {
  strategy: "skip" | "rename" | "update";
  filename?: string;
};
type UploadConflictReview = {
  files: Array<File | FolderUploadEntry>;
  categoryId: string;
  uploadMode: "files" | "folder";
  allowFolderMerge: boolean;
  publishIntents?: boolean[];
  preflight: ManagedUploadPreflightResponse;
};

function uploadEntryFile(entry: File | FolderUploadEntry) {
  return "file" in entry ? entry.file : entry;
}

function uploadEntryKey(entry: File | FolderUploadEntry, index: number) {
  const file = uploadEntryFile(entry);
  return `${"relativePath" in entry ? entry.relativePath : file.name}:${file.size}:${index}`;
}

function UploadSelectionList({
  entries,
  defaultPublish,
  overrides,
  canPublish,
  onDefaultChange,
  onOverrideChange,
  onRemove,
}: {
  entries: Array<File | FolderUploadEntry>;
  defaultPublish: boolean;
  overrides: Record<string, boolean>;
  canPublish: boolean;
  onDefaultChange: (publish: boolean) => void;
  onOverrideChange: (key: string, publish: boolean) => void;
  onRemove: (index: number) => void;
}) {
  return (
    <div className="space-y-2">
      <label className="block space-y-1 text-ui-xs font-medium">
        <span>批量默认处理</span>
        <Select
          value={defaultPublish ? "publish" : "upload"}
          onChange={(event) =>
            onDefaultChange(event.target.value === "publish")
          }
        >
          <option value="upload">仅上传</option>
          {canPublish && <option value="publish">上传并发布</option>}
        </Select>
      </label>
      <ul className="max-h-56 space-y-2 overflow-y-auto rounded-ui-md border border-border p-2 text-ui-sm">
        {entries.map((entry, index) => {
          const file = uploadEntryFile(entry);
          const key = uploadEntryKey(entry, index);
          const video = /\.mp4$/i.test(file.name);
          const publish = video ? false : (overrides[key] ?? defaultPublish);
          return (
            <li
              key={key}
              className="grid gap-2 rounded-ui-sm border border-border/70 p-2 sm:grid-cols-[minmax(0,1fr)_10rem_2rem] sm:items-center"
            >
              <div className="min-w-0">
                <p className="break-all font-medium">
                  {"relativePath" in entry ? entry.relativePath : file.name}
                </p>
                <p className="text-ui-xs text-muted-foreground">
                  {formatUploadSize(file.size)}
                </p>
              </div>
              <Select
                aria-label={`${file.name}处理方式`}
                value={publish ? "publish" : "upload"}
                disabled={video || !canPublish}
                onChange={(event) =>
                  onOverrideChange(key, event.target.value === "publish")
                }
              >
                <option value="upload">
                  {video ? "仅上传（待转录）" : "仅上传"}
                </option>
                {!video && canPublish && (
                  <option value="publish">上传并发布</option>
                )}
              </Select>
              <IconButton
                type="button"
                label={`移除 ${file.name}`}
                onClick={() => onRemove(index)}
              >
                <Trash2 className="size-4" />
              </IconButton>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function renameUploadRoots(
  files: Array<File | FolderUploadEntry>,
  rootRenames: Record<string, string>,
): Array<File | FolderUploadEntry> {
  return files.map((entry) => {
    if (!("file" in entry)) return entry;
    const parts = entry.relativePath.split("/");
    const renamed = rootRenames[parts[0]]?.trim();
    if (renamed) parts[0] = renamed;
    return { ...entry, relativePath: parts.join("/") };
  });
}
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
  return [hours, minutes, seconds]
    .map((value) => String(value).padStart(2, "0"))
    .join(":");
}

function folderContentSummary(folder: ManagedCategory) {
  const directChildCount = folder.direct_child_count ?? 0;
  const totalChildCount = folder.total_child_count ?? directChildCount;
  const totalItemCount = folder.total_item_count ?? folder.item_count;
  const childLabel =
    directChildCount > 0 ? ` · ${directChildCount} 个直接子文件夹` : "";
  const nestedLabel =
    totalChildCount > directChildCount
      ? ` · 共 ${totalChildCount} 个子文件夹`
      : "";
  const totalLabel =
    totalItemCount !== folder.item_count
      ? ` · 共 ${totalItemCount} 份资料`
      : "";
  return `${folder.item_count} 份直接资料${childLabel}${nestedLabel}${totalLabel}`;
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

function UploadTaskDetailSheet({
  detail,
  loading,
  canRetry,
  onRetry,
  onClose,
}: {
  detail: ManagedUploadTask | null;
  loading: boolean;
  canRetry: (task: ManagedUploadTask) => boolean;
  onRetry: (task: ManagedUploadTask) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "accepted" | "skipped">("all");
  const entries = detail?.entries || [];
  const visibleEntries = entries.filter((entry) => {
    const matchesFilter = filter === "all" || entry.status === filter;
    const name = entry.relative_path || entry.filename;
    return (
      matchesFilter &&
      (!query.trim() ||
        name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
    );
  });
  const total = Math.max(0, detail?.total_files || 0);
  const accepted = Math.max(0, detail?.accepted_files || 0);
  const skipped = Math.max(0, detail?.skipped_files || 0);
  const unresolved = Math.max(0, total - accepted - skipped);
  const processed = accepted + skipped;
  const progress =
    total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;

  useEffect(() => {
    if (!detail) return;
    setQuery("");
    setFilter("all");
  }, [detail?.batch_id]);

  return (
    <Sheet
      open={Boolean(detail)}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent className="flex w-full max-w-2xl flex-col overflow-hidden p-0 sm:max-w-2xl">
        <SheetHeader className="border-b border-border py-5 pl-5 pr-16 sm:pl-6 sm:pr-16">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <SheetTitle>上传任务详情</SheetTitle>
              <SheetDescription className="mt-1 break-all">
                {detail?.target_path || "查看任务明细"}
              </SheetDescription>
            </div>
            {detail && (
              <Badge
                className="shrink-0"
                variant={uploadTaskStatusVariant(detail.status)}
              >
                {uploadTaskStatusLabel[detail.status]}
              </Badge>
            )}
          </div>
        </SheetHeader>
        {loading ? (
          <LoadingState className="m-6 border-0" label="正在加载任务详情…" />
        ) : (
          detail && (
            <>
              <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-6">
                <div className="space-y-5">
                  <section aria-labelledby="upload-task-overview-title">
                    <h3
                      id="upload-task-overview-title"
                      className="text-ui-sm font-semibold"
                    >
                      任务概览
                    </h3>
                    <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                      {[
                        { label: "全部文件", value: total },
                        { label: "已接收", value: accepted },
                        { label: "已跳过", value: skipped },
                        { label: "未完成", value: unresolved },
                      ].map((item) => (
                        <div
                          key={item.label}
                          className="rounded-ui-md border border-border bg-surface-muted/30 px-3 py-2.5"
                        >
                          <p className="text-ui-xs text-muted-foreground">
                            {item.label}
                          </p>
                          <p className="mt-1 text-ui-lg font-semibold tabular-nums">
                            {item.value}
                          </p>
                        </div>
                      ))}
                    </div>
                    <div className="mt-4">
                      <div className="flex items-center justify-between text-ui-xs">
                        <span className="text-muted-foreground">处理进度</span>
                        <span className="tabular-nums">{progress}%</span>
                      </div>
                      <div
                        className="mt-1.5 h-2 overflow-hidden rounded-full bg-surface-muted"
                        role="progressbar"
                        aria-valuenow={progress}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-label={`任务处理进度 ${progress}%`}
                      >
                        <span
                          className={`block h-full ${unresolved > 0 ? "bg-warning" : "bg-success"}`}
                          style={{ width: `${progress}%` }}
                        />
                      </div>
                    </div>
                  </section>
                  <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-2 border-y border-border py-4 text-ui-sm">
                    <dt className="text-muted-foreground">上传类型</dt>
                    <dd>
                      {detail.upload_mode === "folder"
                        ? "文件夹上传"
                        : "文件上传"}
                    </dd>
                    <dt className="text-muted-foreground">创建人</dt>
                    <dd>{detail.created_by_name}</dd>
                    <dt className="text-muted-foreground">创建时间</dt>
                    <dd>{formatManagedUpdatedAt(detail.created_at)}</dd>
                    {detail.error_summary && (
                      <>
                        <dt className="text-muted-foreground">处理说明</dt>
                        <dd className="break-words text-destructive">
                          {detail.error_summary}
                        </dd>
                      </>
                    )}
                  </dl>
                  {detail.error_summary && (
                    <div
                      className="rounded-ui-md border border-warning/40 bg-warning/10 px-3 py-2.5 text-ui-sm"
                      role="alert"
                    >
                      <p className="font-medium">任务包含未接收文件</p>
                      <p className="mt-1 text-muted-foreground">
                        请在下方按状态筛选并查看每个文件的处理原因。
                      </p>
                    </div>
                  )}
                  <section aria-labelledby="upload-task-files-title">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                      <div>
                        <h3
                          id="upload-task-files-title"
                          className="text-ui-sm font-semibold"
                        >
                          文件明细
                        </h3>
                        <p className="mt-1 text-ui-xs text-muted-foreground">
                          显示 {visibleEntries.length} / {entries.length} 个文件
                        </p>
                      </div>
                      <Input
                        className="h-control-sm w-full sm:w-56"
                        type="search"
                        aria-label="搜索任务文件"
                        placeholder="搜索文件名…"
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                      />
                    </div>
                    <div
                      className="mt-3 flex flex-wrap gap-2"
                      role="group"
                      aria-label="文件状态筛选"
                    >
                      {[
                        { key: "all", label: `全部 ${entries.length}` },
                        { key: "accepted", label: `已接收 ${accepted}` },
                        { key: "skipped", label: `已跳过 ${skipped}` },
                      ].map((item) => (
                        <Button
                          key={item.key}
                          size="sm"
                          variant={
                            filter === item.key ? "secondary" : "outline"
                          }
                          aria-pressed={filter === item.key}
                          onClick={() => setFilter(item.key as typeof filter)}
                        >
                          {item.label}
                        </Button>
                      ))}
                    </div>
                    <ul className="mt-3 divide-y divide-border rounded-ui-md border border-border">
                      {visibleEntries.length > 0 ? (
                        visibleEntries.map((entry) => (
                          <li
                            key={entry.sequence}
                            className="flex items-start gap-3 px-3 py-3"
                          >
                            <span
                              className={`mt-0.5 size-2 shrink-0 rounded-full ${entry.status === "accepted" ? "bg-success" : "bg-warning"}`}
                              aria-hidden="true"
                            />
                            <span className="min-w-0 flex-1">
                              <span
                                className="block break-all text-ui-sm"
                                title={entry.relative_path || entry.filename}
                              >
                                {entry.relative_path || entry.filename}
                              </span>
                              {entry.reason && (
                                <span className="mt-1 block break-words text-ui-xs text-muted-foreground">
                                  {entry.reason}
                                </span>
                              )}
                            </span>
                            <Badge
                              className="shrink-0"
                              variant={
                                entry.status === "accepted"
                                  ? "success"
                                  : "warning"
                              }
                            >
                              {uploadTaskEntryStatus(entry)}
                            </Badge>
                          </li>
                        ))
                      ) : (
                        <li className="px-3 py-8 text-center text-ui-sm text-muted-foreground">
                          没有符合条件的文件
                        </li>
                      )}
                    </ul>
                  </section>
                </div>
              </div>
              {detail.status === "failed" && (
                <div className="border-t border-border px-5 py-4 sm:px-6">
                  <Button
                    variant="outline"
                    disabled={!canRetry(detail)}
                    aria-label={
                      canRetry(detail)
                        ? "重试此任务"
                        : "重试此任务（原始文件不可用）"
                    }
                    title={
                      !canRetry(detail)
                        ? "原始文件仅保留在当前浏览器会话中，当前不可重试"
                        : undefined
                    }
                    onClick={() => {
                      onRetry(detail);
                      onClose();
                    }}
                  >
                    <RotateCcw className="size-4" />
                    重试此任务
                  </Button>
                </div>
              )}
            </>
          )
        )}
      </SheetContent>
    </Sheet>
  );
}

function UploadTaskResult({ task }: { task: ManagedUploadTask }) {
  const total = Math.max(0, task.total_files);
  const accepted = Math.min(total, Math.max(0, task.accepted_files));
  const skipped = Math.min(total - accepted, Math.max(0, task.skipped_files));
  const unresolved = Math.max(0, total - accepted - skipped);
  const processed = accepted + skipped;
  const processedPercent =
    total > 0 ? Math.round((processed / total) * 100) : 0;
  const acceptedPercent = total > 0 ? (accepted / total) * 100 : 0;
  const skippedPercent = total > 0 ? (skipped / total) * 100 : 0;
  const unresolvedPercent = total > 0 ? (unresolved / total) * 100 : 0;

  const summary =
    task.status === "processing"
      ? `已处理 ${processed} / ${total} 个`
      : task.status === "completed"
        ? `已接收 ${accepted} / ${total} 个`
        : [
            accepted > 0 ? `已接收 ${accepted} 个` : null,
            skipped > 0 ? `跳过 ${skipped} 个` : null,
            unresolved > 0 ? `未完成 ${unresolved} 个` : null,
          ]
            .filter(Boolean)
            .join(" · ") || "没有文件被接收";

  return (
    <div className="min-w-0">
      <div className="flex items-center justify-between gap-2 text-ui-xs">
        <span className="text-muted-foreground">{summary}</span>
        {task.status === "processing" && (
          <span className="shrink-0 tabular-nums text-muted-foreground">
            {processedPercent}%
          </span>
        )}
      </div>
      <div
        className="mt-1.5 flex h-1.5 overflow-hidden rounded-full bg-surface-muted"
        role="img"
        aria-label={`处理结果：${summary}`}
      >
        {acceptedPercent > 0 && (
          <span
            className="h-full bg-success"
            style={{ width: `${acceptedPercent}%` }}
          />
        )}
        {skippedPercent > 0 && (
          <span
            className="h-full bg-warning"
            style={{ width: `${skippedPercent}%` }}
          />
        )}
        {task.status === "failed" && unresolvedPercent > 0 && (
          <span
            className="h-full bg-destructive"
            style={{ width: `${unresolvedPercent}%` }}
          />
        )}
        {task.status === "processing" && processed === 0 && (
          <span className="h-full w-1/4 animate-pulse bg-primary" />
        )}
      </div>
    </div>
  );
}

function csvCell(value: string | number | null | undefined) {
  const text = String(value ?? "");
  const safeText = /^[=+\-@]/.test(text) ? `'${text}` : text;
  return `"${safeText.replace(/"/g, '""')}"`;
}

function uploadTaskSummaryCsv(tasks: ManagedUploadTask[]) {
  const header = [
    "任务类型",
    "目标路径",
    "文件总数",
    "已接收",
    "已跳过",
    "未完成",
    "状态",
    "创建人",
    "创建时间",
    "失败原因",
  ];
  const rows = tasks.map((task) => {
    const unresolved = Math.max(
      0,
      task.total_files - task.accepted_files - task.skipped_files,
    );
    return [
      task.upload_mode === "folder" ? "文件夹上传" : "文件上传",
      task.target_path,
      task.total_files,
      task.accepted_files,
      task.skipped_files,
      unresolved,
      uploadTaskStatusLabel[task.status] || task.status,
      task.created_by_name,
      formatManagedUpdatedAt(task.created_at),
      task.error_summary,
    ]
      .map(csvCell)
      .join(",");
  });
  return [header.map(csvCell).join(","), ...rows].join("\r\n");
}

function UploadTasksPanel({
  activeUpload,
  canRetry,
  onRetry,
  canTranscribe,
}: {
  activeUpload: ActiveUploadState | null;
  canRetry: (task: ManagedUploadTask) => boolean;
  onRetry: (task: ManagedUploadTask) => void;
  canTranscribe: boolean;
}) {
  const [tasks, setTasks] = useState<ManagedUploadTask[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<
    "all" | "active" | "completed" | "partial_success" | "failed"
  >("all");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(UPLOAD_TASK_PAGE_SIZE_OPTIONS[0]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<ManagedUploadTask | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [transcriptionTask, setTranscriptionTask] = useState<ManagedUploadTask | null>(null);
  const [schemeId, setSchemeId] = useState("");
  const [schemes, setSchemes] = useState<TranscriptionSchemeOption[]>([]);
  const [preflight, setPreflight] = useState<Awaited<ReturnType<typeof adminContentApi.preflightBulkTranscription>> | null>(null);
  const [transcriptionBusy, setTranscriptionBusy] = useState(false);
  const [requestKey, setRequestKey] = useState("");
  useEffect(() => {
    setPage(0);
    setSelected([]);
  }, [query, statusFilter, pageSize]);

  const loadTasks = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await adminContentApi.uploadTasks({
        status:
          statusFilter === "all"
            ? undefined
            : statusFilter === "active"
              ? "processing"
              : statusFilter,
        query: query || undefined,
        limit: pageSize,
        offset: page * pageSize,
      });
      setTasks(result.tasks);
      setTotal(result.total);
      setCounts(result.status_counts);
    } catch (loadError) {
      setError(
        loadError instanceof Error ? loadError.message : "上传任务加载失败",
      );
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, query, statusFilter]);

  useManagedContentLiveRefresh({
    active: tasks.some((task) => task.status === "processing"),
    refresh: loadTasks,
    enabled: true,
  });
  useEffect(() => {
    void loadTasks();
  }, [loadTasks]);
  useEffect(() => {
    if (
      !activeUpload ||
      activeUpload.phase === "uploading" ||
      activeUpload.phase === "processing"
    )
      return;
    void loadTasks();
  }, [activeUpload, loadTasks]);

  const openDetail = async (task: ManagedUploadTask) => {
    setDetail(task);
    setDetailLoading(true);
    try {
      setDetail(await adminContentApi.uploadTask(task.batch_id));
    } catch (detailError) {
      setError(
        detailError instanceof Error
          ? detailError.message
          : "上传任务详情加载失败",
      );
    } finally {
      setDetailLoading(false);
    }
  };
  const openBatchTranscription = async (task: ManagedUploadTask) => {
    setTranscriptionTask(task);
    setSchemeId("");
    setPreflight(null);
    setRequestKey(createRequestId());
    try {
      setSchemes(await adminContentApi.transcriptionSchemes());
    } catch (loadError) {
      toast.error(loadError instanceof Error ? loadError.message : "转录方案加载失败");
    }
  };
  const checkBatchTranscription = async () => {
    if (!transcriptionTask || !schemeId || !requestKey) return;
    setTranscriptionBusy(true);
    try {
      setPreflight(await adminContentApi.preflightBulkTranscription({ scheme_id: schemeId, request_idempotency_key: requestKey, upload_batch_id: transcriptionTask.batch_id }));
    } catch (checkError) {
      toast.error(checkError instanceof Error ? checkError.message : "转录预检失败");
    } finally { setTranscriptionBusy(false); }
  };
  const startBatchTranscription = async () => {
    if (!transcriptionTask || !schemeId || !requestKey || !preflight) return;
    setTranscriptionBusy(true);
    try {
      const result = await adminContentApi.bulkStartTranscription({ scheme_id: schemeId, request_idempotency_key: requestKey, upload_batch_id: transcriptionTask.batch_id });
      result.failed ? toast.error(`已启动 ${result.started} 个，${result.failed} 个未启动`) : toast.success(`已启动 ${result.started} 个视频转录任务`);
      setTranscriptionTask(null);
      await loadTasks();
    } catch (startError) {
      toast.error(startError instanceof Error ? startError.message : "启动转录失败");
    } finally { setTranscriptionBusy(false); }
  };
  const visibleTasks = tasks;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const selectableTasks = visibleTasks;
  const allSelected =
    selectableTasks.length > 0 &&
    selectableTasks.every((task) => selected.includes(task.batch_id));
  const selectedTasks = visibleTasks.filter((task) =>
    selected.includes(task.batch_id),
  );
  const changePage = (nextPage: number) => {
    setSelected([]);
    setPage(Math.max(0, Math.min(nextPage, pageCount - 1)));
  };
  const toggleAll = () => {
    setSelected(
      allSelected
        ? []
        : selectableTasks.map((task) => task.batch_id),
    );
  };
  const toggleTask = (batchId: string) => {
    setSelected((current) =>
      current.includes(batchId)
        ? current.filter((id) => id !== batchId)
        : [...current, batchId],
    );
  };
  const exportSelected = () => {
    if (selectedTasks.length === 0) return;
    const date = new Date().toISOString().slice(0, 10);
    triggerManagedDownload(
      new Blob([`\uFEFF${uploadTaskSummaryCsv(selectedTasks)}`], {
        type: "text/csv;charset=utf-8",
      }),
      `上传任务摘要-${date}.csv`,
    );
    toast.success(`已导出 ${selectedTasks.length} 个任务摘要`);
  };
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
  const progressPercent =
    activeUpload && activeUpload.totalBytes > 0
      ? Math.min(
          100,
          Math.round(
            (activeUpload.loadedBytes / activeUpload.totalBytes) * 100,
          ),
        )
      : activeUpload?.phase === "completed"
        ? 100
        : 0;
  const activeLabel =
    activeUpload?.phase === "uploading"
      ? "上传中"
      : activeUpload?.phase === "processing"
        ? "服务端处理中…"
        : activeUpload?.phase === "completed"
          ? "已完成"
          : "上传失败";
  const activeStatusIcon =
    activeUpload?.phase === "failed" ? (
      <XCircle className="size-4 text-destructive" />
    ) : activeUpload?.phase === "completed" ? (
      <CheckCircle2 className="size-4 text-success" />
    ) : (
      <Upload className="size-4 animate-pulse text-primary" />
    );

  return (
    <section className="space-y-4" aria-labelledby="upload-tasks-title">
      <div
        className="grid grid-cols-2 gap-3 sm:grid-cols-4"
        aria-label="上传任务状态概览"
      >
        {[
          {
            key: "active",
            label: "进行中",
            value:
              (counts.processing || 0) +
              (activeUpload &&
              ["uploading", "processing"].includes(activeUpload.phase)
                ? 1
                : 0),
            icon: <Upload className="size-4" />,
            tone: "primary" as const,
          },
          {
            key: "completed",
            label: "已完成",
            value: counts.completed || 0,
            icon: <CheckCircle2 className="size-4" />,
            tone: "success" as const,
          },
          {
            key: "partial_success",
            label: "部分成功",
            value: counts.partial_success || 0,
            icon: <AlertTriangle className="size-4" />,
            tone: "warning" as const,
          },
          {
            key: "failed",
            label: "失败",
            value: counts.failed || 0,
            icon: <XCircle className="size-4" />,
            tone: "destructive" as const,
          },
        ].map((summary) => (
          <ManagedSummaryCard
            key={summary.key}
            label={summary.label}
            value={summary.value}
            icon={summary.icon}
            tone={summary.tone}
            active={statusFilter === summary.key}
            onClick={() =>
              setStatusFilter((current) =>
                current === summary.key
                  ? "all"
                  : (summary.key as typeof statusFilter),
              )
            }
          />
        ))}
      </div>
      {activeUpload && (
        <div
          className="rounded-ui-lg border border-primary/40 bg-primary/5 px-4 py-3"
          role="status"
          aria-live="polite"
        >
          <div className="flex items-start gap-3">
            {activeStatusIcon}
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-medium">{activeLabel}</p>
                <span className="text-ui-xs tabular-nums text-muted-foreground">
                  {progressPercent}%
                </span>
              </div>
              <p className="mt-1 break-words text-ui-xs text-muted-foreground">
                {activeUpload.uploadMode === "folder" ? "文件夹" : "文件"} ·{" "}
                {activeUpload.totalFiles} 个 · {activeUpload.targetPath}
              </p>
              <div className="mt-2 h-2 overflow-hidden rounded-full bg-primary/15">
                <div
                  className="h-full rounded-full bg-primary transition-[width] duration-normal"
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
              {activeUpload.message && (
                <p className="mt-2 break-words text-ui-xs text-destructive">
                  {activeUpload.message}
                </p>
              )}
            </div>
          </div>
        </div>
      )}
      <Card className="overflow-hidden shadow-surface">
        <div className="grid gap-3 border-b border-border px-4 py-4 sm:px-5 xl:grid-cols-[minmax(13rem,1fr)_24rem_auto] xl:items-end" data-testid="upload-task-toolbar">
          <div>
            <h2
              id="upload-tasks-title"
              className="text-ui-base font-semibold tracking-tight"
            >
              上传任务
            </h2>
            <p className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-ui-xs text-muted-foreground"><span>共 {total} 个任务</span><span role="status" aria-live="polite">· {selected.length > 0 ? <>已选择 <strong>{selected.length}</strong> 个</> : <>未选择任务</>}</span></p>
          </div>
          <form
            className="min-w-0"
            role="search"
            onSubmit={(event) => {
              event.preventDefault();
              applySearch();
            }}
          >
            <label className="block min-w-0 text-ui-xs text-muted-foreground">
              <span className="sr-only">搜索任务</span>
              <span className="relative block">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  type="search"
                  aria-label="搜索上传任务"
                  className="h-control-md pl-9 pr-10"
                  value={queryInput}
                  onChange={(event) => setQueryInput(event.target.value)}
                  placeholder="搜索目标目录或文件名..."
                />
                <SlidersHorizontal className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
              </span>
            </label>
          </form>
          <div className="flex shrink-0 flex-wrap items-center gap-2 xl:justify-end">
            <Button size="sm" variant="outline" onClick={() => void loadTasks()} disabled={loading}>
              <RefreshCw
                className={loading ? "size-4 animate-spin" : "size-4"}
              />
              刷新列表
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={clearFilters}
              disabled={!queryInput && !hasFilters}
            >
              清除筛选
            </Button>
            {selected.length > 0 && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => setSelected([])}
              >
                取消选择
              </Button>
            )}
            {selected.length > 1 && (
              <BatchActionsMenu
                disabled={loading}
                options={[
                  {
                    key: "export_upload_summary",
                    label: "导出任务摘要",
                    icon: <Download className="size-4" />,
                    onSelect: exportSelected,
                  },
                ]}
              />
            )}
          </div>
        </div>
        {error && (
          <ErrorState
            title="上传任务加载失败"
            description={error}
            action={
              <Button
                size="sm"
                variant="outline"
                onClick={() => void loadTasks()}
              >
                重新加载
              </Button>
            }
          />
        )}
        {loading ? (
          <LoadingState
            className="min-h-48 border-0"
            label="正在加载上传任务…"
          />
        ) : statusFilter === "active" &&
          !activeUpload &&
          visibleTasks.length === 0 ? (
          <EmptyState
            className="rounded-none border-0"
            title="暂无进行中的上传"
            description="开始上传文件或文件夹后，进度会显示在这里。"
            action={
              <Button size="sm" variant="outline" onClick={clearFilters}>
                查看全部任务
              </Button>
            }
          />
        ) : visibleTasks.length === 0 ? (
          <EmptyState
            className="rounded-none border-0"
            title={hasFilters ? "没有符合条件的上传任务" : "暂无上传任务"}
            description={
              hasFilters
                ? "请调整搜索关键词或状态筛选。"
                : "上传文件或文件夹后，任务记录会显示在这里。"
            }
            action={
              hasFilters ? (
                <Button size="sm" variant="outline" onClick={clearFilters}>
                  清除筛选
                </Button>
              ) : undefined
            }
          />
        ) : (
          <>
            <div
              className="hidden grid-cols-[2rem_minmax(10rem,1.7fr)_7.5rem_minmax(11rem,1fr)_8rem_6rem_12rem] gap-x-4 border-b border-border bg-surface-muted/40 px-5 py-2.5 text-ui-xs font-medium text-muted-foreground xl:grid"
              data-testid="upload-task-header"
            >
              <Checkbox
                aria-label="选择当前页上传任务"
                checked={allSelected}
                disabled={selectableTasks.length === 0}
                onChange={toggleAll}
              />
              <span>任务</span>
              <span>文件</span>
              <span>处理结果</span>
              <span>创建时间</span>
              <span>状态</span>
              <span className="text-right">操作</span>
            </div>
            <ul className="divide-y divide-border">
              {visibleTasks.map((task) => (
                <li
                  key={task.batch_id}
                  className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-3 px-4 py-4 sm:px-5 xl:grid-cols-[2rem_minmax(10rem,1.7fr)_7.5rem_minmax(11rem,1fr)_8rem_6rem_12rem] xl:items-center xl:gap-x-4"
                  data-testid="upload-task-row"
                >
                  <Checkbox
                    className="mt-0.5 xl:mt-0"
                    aria-label={`选择${task.upload_mode === "folder" ? "文件夹上传" : "文件上传"} ${task.target_path}`}
                    checked={selected.includes(task.batch_id)}
                    disabled={false}
                    onChange={() => toggleTask(task.batch_id)}
                  />
                  <div className="min-w-0">
                    <p className="break-all font-medium">
                      {task.upload_mode === "folder"
                        ? "文件夹上传"
                        : "文件上传"}
                    </p>
                    <p className="mt-1 break-all text-ui-xs text-muted-foreground">
                      {task.target_path}
                    </p>
                  </div>
                  <div className="col-span-2 xl:col-span-1">
                    <span className="mb-1 block text-ui-xs font-medium text-muted-foreground xl:hidden">
                      文件
                    </span>
                    <span className="block text-ui-xs text-muted-foreground">
                      {task.upload_mode === "folder" ? "文件夹" : "文件"} ·{" "}
                      {task.total_files} 个
                    </span>
                    {task.video_count > 0 && <span className="mt-1 block text-ui-xs text-muted-foreground">{task.video_count} 个视频 · {task.transcribable_video_count} 个待转录</span>}
                  </div>
                  <div className="col-span-2 xl:col-span-1">
                    <span className="mb-1 block text-ui-xs font-medium text-muted-foreground xl:hidden">
                      处理结果
                    </span>
                    <UploadTaskResult task={task} />
                  </div>
                  <div className="col-span-2 xl:col-span-1">
                    <span className="mb-1 block text-ui-xs font-medium text-muted-foreground xl:hidden">
                      创建时间
                    </span>
                    <span className="text-ui-xs text-muted-foreground">
                      {formatManagedUpdatedAt(task.created_at)}
                    </span>
                  </div>
                  <div className="col-span-2 xl:col-span-1">
                    <span className="mb-1 block text-ui-xs font-medium text-muted-foreground xl:hidden">
                      状态
                    </span>
                    <Badge variant={uploadTaskStatusVariant(task.status)}>
                      {uploadTaskStatusLabel[task.status]}
                    </Badge>
                  </div>
                  <div className="col-span-2 flex flex-wrap gap-2 xl:col-span-1 xl:justify-end">
                    <>
                      {canTranscribe && task.video_count > 0 && <Button size="sm" variant="outline" disabled={task.transcribable_video_count === 0} title={task.transcribable_video_count === 0 ? "此批次视频均已转录或正在转录" : undefined} onClick={() => void openBatchTranscription(task)}><Rocket className="size-4" />转录此批次视频</Button>}
                      {task.status === "failed" && (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={!canRetry(task)}
                          aria-label={
                            canRetry(task) ? "重试" : "重试（原始文件不可用）"
                          }
                          title={
                            !canRetry(task)
                              ? "原始文件仅保留在当前浏览器会话中，当前不可重试"
                              : undefined
                          }
                          onClick={() => onRetry(task)}
                        >
                          <RotateCcw className="size-4" />
                          重试
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => void openDetail(task)}
                      >
                        <ListChecks className="size-4" />
                        详情
                      </Button>
                    </>
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}
        <div className="flex flex-col gap-2 border-t border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5">
          <p className="text-ui-xs text-muted-foreground">
            共 {total} 个任务，第 {page + 1} / {pageCount} 页
          </p>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <label className="flex items-center gap-2 text-ui-xs text-muted-foreground">
              每页
              <Select
                aria-label="每页上传任务条数"
                className="h-control-sm w-20"
                value={String(pageSize)}
                onChange={(event) => setPageSize(Number(event.target.value))}
              >
                {UPLOAD_TASK_PAGE_SIZE_OPTIONS.map((size) => (
                  <option key={size} value={size}>
                    {size} 条
                  </option>
                ))}
              </Select>
            </label>
            <Button
              size="sm"
              variant="outline"
              disabled={page === 0 || loading}
              onClick={() => changePage(page - 1)}
            >
              上一页
            </Button>
            <Select
              aria-label="跳转上传任务页码"
              className="h-control-sm w-24"
              value={String(page + 1)}
              onChange={(event) => changePage(Number(event.target.value) - 1)}
              disabled={loading}
            >
              {Array.from({ length: pageCount }, (_, index) => (
                <option key={index + 1} value={index + 1}>
                  第 {index + 1} 页
                </option>
              ))}
            </Select>
            <Button
              size="sm"
              variant="outline"
              disabled={page + 1 >= pageCount || loading}
              onClick={() => changePage(page + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      </Card>
      <UploadTaskDetailSheet
        detail={detail}
        loading={detailLoading}
        canRetry={canRetry}
        onRetry={onRetry}
        onClose={() => setDetail(null)}
      />
      <Dialog open={Boolean(transcriptionTask)} onOpenChange={(open) => { if (!open && !transcriptionBusy) setTranscriptionTask(null); }}>
        <DialogContent className="max-w-2xl"><DialogHeader><DialogTitle>转录上传批次视频</DialogTitle><DialogDescription>视频仍保留在资料库；确认方案后才会创建转录任务。</DialogDescription></DialogHeader>
          <div className="space-y-4"><label className="block space-y-1.5 text-ui-sm font-medium"><span>转录方案</span><Select aria-label="选择转录方案" value={schemeId} onChange={(event) => { setSchemeId(event.target.value); setPreflight(null); }}><option value="">请选择可用方案</option>{schemes.map((scheme) => <option key={scheme.scheme_id} value={scheme.scheme_id} disabled={!scheme.enabled || scheme.archived || scheme.availability !== "available"}>{scheme.name}{scheme.availability !== "available" ? "（不可用）" : ""}</option>)}</Select></label><p className="rounded-ui-md border border-border bg-surface-muted/40 px-3 py-2 text-ui-sm">范围：此上传批次，服务端会汇总各子目录中的待转录视频。</p>{preflight && <p className="text-ui-sm">可启动 <strong>{preflight.ready_count}</strong> 个，已跳过/不可用 <strong>{preflight.blocked_count}</strong> 个。</p>}</div>
          <DialogFooter><Button variant="outline" disabled={transcriptionBusy} onClick={() => setTranscriptionTask(null)}>取消</Button>{!preflight ? <Button disabled={!schemeId || transcriptionBusy} onClick={() => void checkBatchTranscription()}>{transcriptionBusy ? "检查中…" : "检查可转录视频"}</Button> : <Button disabled={transcriptionBusy || preflight.ready_count === 0} onClick={() => void startBatchTranscription()}>{transcriptionBusy ? "启动中…" : `启动转录（${preflight.ready_count}）`}</Button>}</DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

const statusLabel: Record<string, string> = {
  draft: "待提交",
  awaiting_review: "待确认",
  approved: "已确认",
  rejected: "已退回",
  publishing: "发布中",
  published: "已发布",
  publication_failed: "发布失败",
  superseded: "历史版本",
  awaiting_transcription: "待转录",
  transcribing: "转录中",
  transcription_failed: "转录失败",
  transcript_ready: "转录稿待审核",
};
const sourceLabel: Record<string, string> = {
  web: "网页上传",
  server: "后台导入",
  legacy: "历史迁移",
  transcription: "视频转写",
};

const documentTypeOptions = [
  ["pdf", "PDF"],
  ["docx", "Word"],
  ["xlsx", "Excel"],
  ["pptx", "PPT"],
  ["xmind", "XMind"],
  ["markdown", "Markdown"],
  ["transcript", "视频转录稿"],
  ["other", "其他"],
] as const;

function statusVariant(status: string) {
  if (status === "published") return "success" as const;
  if (status.includes("failed") || status === "rejected")
    return "destructive" as const;
  if (status === "awaiting_review" || status === "publishing")
    return "warning" as const;
  return "secondary" as const;
}

function PublicationFailure({ item }: { item: ManagedContentItem }) {
  const failure = item.publication_failure;
  if (!failure) return null;
  return (
    <div
      className="mt-2 max-w-md space-y-1 text-ui-xs text-destructive"
      role="alert"
    >
      <p className="break-words">{failure.message}</p>
      <p className="break-words text-muted-foreground">
        {failure.recommended_action}
      </p>
      <p className="break-words text-muted-foreground">
        {failure.retryable
          ? "可以重新发布"
          : "按原失败原因直接重试通常不会成功；系统或文件处理后可重新发布"}
        {item.publication_attempt_count > 1
          ? ` · 共尝试 ${item.publication_attempt_count} 次`
          : ""}
      </p>
    </div>
  );
}

function ManagedItemIdentity({
  item,
  showCategoryPath = false,
}: {
  item: ManagedContentItem;
  showCategoryPath?: boolean;
}) {
  const isMediaTranscript = item.content_kind === "media_transcript";
  const mediaDetails = isMediaTranscript
    ? [
        formatMediaDuration(item.media_duration_ms),
        item.media_file_size != null
          ? formatUploadSize(item.media_file_size)
          : null,
      ].filter(Boolean)
    : [];
  return (
    <div className="min-w-0">
      <p className="break-words font-medium">{item.title}</p>
      <p className="mt-0.5 break-all text-ui-xs text-muted-foreground">
        {item.original_filename} · v{item.version_number}
        {mediaDetails.length ? ` · ${mediaDetails.join(" · ")}` : ""}
      </p>
      {showCategoryPath && (
        <p className="mt-1 break-words text-ui-xs text-muted-foreground">
          目录：{item.category_path || item.category_label}
        </p>
      )}
      {isMediaTranscript && item.has_pending_revision && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Badge variant="warning">有新转录稿待处理</Badge>
        </div>
      )}
    </div>
  );
}

type BulkAction =
  | "move"
  | "submit"
  | "approve"
  | "reject"
  | "publish"
  | "download"
  | "archive"
  | "export_upload_summary";
type BulkWorkbenchResult = "submitted" | "approved" | "rejected";

type FilenameConflict = {
  item_id: string;
  version_id: string;
  title: string;
  original_filename: string;
  lifecycle_status: string;
  has_published_head: boolean;
};

function filenameForOldMode(
  originalFilename: string,
  incomingFilename: string,
) {
  const incomingDot = incomingFilename.lastIndexOf(".");
  const originalDot = originalFilename.lastIndexOf(".");
  if (incomingDot <= 0 || incomingDot === incomingFilename.length - 1)
    return originalFilename;
  const incomingSuffix = incomingFilename
    .slice(incomingDot)
    .toLocaleLowerCase("en-US");
  const originalSuffix =
    originalDot > 0
      ? originalFilename.slice(originalDot).toLocaleLowerCase("en-US")
      : "";
  return originalSuffix === incomingSuffix
    ? originalFilename
    : `${originalDot > 0 ? originalFilename.slice(0, originalDot) : originalFilename}${incomingSuffix}`;
}

function filenameConflictFrom(error: unknown): FilenameConflict | null {
  const candidate = error as { code?: unknown; body?: unknown } | null;
  if (
    !candidate ||
    candidate.code !== "content_filename_conflict" ||
    typeof candidate.body !== "string"
  )
    return null;
  try {
    const conflict = JSON.parse(candidate.body)?.detail?.conflict;
    if (
      conflict?.item_id &&
      conflict?.version_id &&
      conflict?.title &&
      conflict?.original_filename
    ) {
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

type ActionMenuOption = {
  key: string;
  label: string;
  icon: ReactNode;
  disabled?: boolean;
  disabledReason?: string;
  destructive?: boolean;
  href?: string;
  onSelect?: () => void;
};

function ActionsMenu({
  disabled,
  options,
  triggerLabel,
  menuLabel,
  compact = false,
}: {
  disabled: boolean;
  options: ActionMenuOption[];
  triggerLabel: string;
  menuLabel: string;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const optionRefs = useRef<Array<HTMLElement | null>>([]);

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        !triggerRef.current?.contains(target) &&
        !menuRef.current?.contains(target)
      )
        setOpen(false);
    };
    const closeEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
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
      Math.max(
        minimumOffset,
        window.innerWidth - menuRect.width - minimumOffset,
      ),
    );
    setPosition({ top, left });
  }, [open, options.length]);

  useEffect(() => {
    if (!open) return;
    const frame = window.requestAnimationFrame(() => {
      optionRefs.current
        .find(
          (option) => option && option.getAttribute("aria-disabled") !== "true",
        )
        ?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [open]);

  const handleMenuKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const enabledOptions = optionRefs.current.filter(
      (option): option is HTMLElement =>
        Boolean(option && option.getAttribute("aria-disabled") !== "true"),
    );
    if (!enabledOptions.length) return;
    const currentIndex = enabledOptions.indexOf(
      document.activeElement as HTMLElement,
    );
    const nextIndex =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? enabledOptions.length - 1
          : event.key === "ArrowDown"
            ? (currentIndex + 1 + enabledOptions.length) % enabledOptions.length
            : (currentIndex - 1 + enabledOptions.length) %
              enabledOptions.length;
    enabledOptions[nextIndex].focus();
  };

  const toggle = () => {
    if (!open && triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      const menuWidth = compact ? 208 : 176;
      const maximumLeft = Math.max(12, window.innerWidth - menuWidth - 12);
      setPosition({
        top: rect.bottom + 6,
        left: Math.min(Math.max(12, rect.right - menuWidth), maximumLeft),
      });
    }
    setOpen((current) => !current);
  };

  return (
    <>
      <Button
        ref={triggerRef}
        size={compact ? "icon" : "sm"}
        variant="outline"
        className={compact ? "!size-9 max-sm:!size-10" : "max-sm:h-control-md"}
        disabled={disabled}
        aria-label={triggerLabel}
        title={triggerLabel}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={toggle}
      >
        {compact ? (
          <MoreHorizontal className="size-4" />
        ) : (
          <>
            {triggerLabel}
            <ChevronDown className="size-4" />
          </>
        )}
      </Button>
      {open &&
        createPortal(
          <div
            ref={menuRef}
            role="menu"
            aria-label={menuLabel}
            className={`fixed z-dropdown overflow-hidden rounded-ui-lg border border-border bg-popover p-1.5 text-popover-foreground shadow-overlay ${compact ? "w-52" : "w-44"}`}
            style={position}
            onKeyDown={handleMenuKeyDown}
          >
            {options.map((option, index) => (
              <div key={option.key}>
                {option.destructive && index > 0 && (
                  <div
                    className="my-1 border-t border-border"
                    role="separator"
                  />
                )}
                {option.href && !option.disabled ? (
                  <a
                    ref={(element) => {
                      optionRefs.current[index] = element;
                    }}
                    role="menuitem"
                    href={option.href}
                    className={`flex w-full items-center gap-2.5 rounded-ui-md px-2.5 py-2 text-left text-ui-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${option.destructive ? "text-destructive hover:bg-destructive/10" : "hover:bg-surface-muted"}`}
                    onClick={() => setOpen(false)}
                  >
                    {option.icon}
                    {option.label}
                  </a>
                ) : (
                  <button
                    ref={(element) => {
                      optionRefs.current[index] = element;
                    }}
                    type="button"
                    role="menuitem"
                    aria-disabled={option.disabled || undefined}
                    disabled={option.disabled}
                    title={option.disabled ? option.disabledReason : undefined}
                    className={`flex w-full items-center gap-2.5 rounded-ui-md px-2.5 py-2 text-left text-ui-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40 ${option.destructive ? "text-destructive hover:bg-destructive/10" : "hover:bg-surface-muted"}`}
                    onClick={() => {
                      setOpen(false);
                      option.onSelect?.();
                    }}
                  >
                    {option.icon}
                    {option.label}
                  </button>
                )}
              </div>
            ))}
          </div>,
          document.body,
        )}
    </>
  );
}

function BatchActionsMenu({
  disabled,
  options,
}: {
  disabled: boolean;
  options: Array<ActionMenuOption & { key: string; onSelect: () => void }>;
}) {
  return (
    <ActionsMenu
      disabled={disabled}
      options={options}
      triggerLabel="批量操作"
      menuLabel="批量操作"
    />
  );
}

function ManagedContentSearchFilters({
  queryInput,
  searchScope,
  currentDirectoryAvailable,
  statusFilter,
  sourceFilter,
  kindFilter,
  onQueryInputChange,
  onSearchScopeChange,
  onStatusFilterChange,
  onSourceFilterChange,
  onKindFilterChange,
  onClear,
}: {
  queryInput: string;
  searchScope: "current" | "global";
  currentDirectoryAvailable: boolean;
  statusFilter: string;
  sourceFilter: string;
  kindFilter: string;
  onQueryInputChange: (value: string) => void;
  onSearchScopeChange: (value: "current" | "global") => void;
  onStatusFilterChange: (value: string) => void;
  onSourceFilterChange: (value: string) => void;
  onKindFilterChange: (value: string) => void;
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const activeFilterCount =
    Number(Boolean(statusFilter)) +
    Number(Boolean(sourceFilter)) +
    Number(Boolean(kindFilter));
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

  const filterButtonLabel = open ? "收起搜索筛选" : "展开搜索筛选";

  return (
    <div
      ref={rootRef}
      className="relative min-w-0 w-full xl:w-72 xl:max-w-72 xl:justify-self-center min-[1400px]:w-96 min-[1400px]:max-w-96"
    >
      <div className="relative">
        <Search
          className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
        <Input
          ref={inputRef}
          className="h-control-sm pl-9 pr-11"
          value={queryInput}
          onChange={(event) => onQueryInputChange(event.target.value)}
          onFocus={() => setOpen(true)}
          aria-label="搜索资料"
          placeholder="搜索资料名称、文件名或目录路径…"
        />
        <button
          type="button"
          className="absolute right-1 top-1/2 flex size-7 -translate-y-1/2 items-center justify-center rounded-ui-sm text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
          aria-label={filterButtonLabel}
          title={filterButtonLabel}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((current) => !current)}
        >
          <SlidersHorizontal className="size-4" aria-hidden="true" />
          {activeFilterCount > 0 && (
            <span
              className="absolute right-0.5 top-0.5 size-1.5 rounded-full bg-primary"
              aria-hidden="true"
            />
          )}
          {activeFilterCount > 0 && (
            <span className="sr-only">，已启用 {activeFilterCount} 项筛选</span>
          )}
        </button>
      </div>
      {open && (
        <div
          id={panelId}
          role="dialog"
          aria-modal="false"
          aria-label="搜索筛选"
          className="absolute inset-x-0 top-full z-dropdown mt-2 rounded-ui-lg border border-border bg-popover p-3 text-popover-foreground shadow-overlay"
        >
          <div className="mb-3 space-y-1">
            <span className="text-ui-xs text-muted-foreground">搜索范围</span>
            <div
              className="grid grid-cols-2 rounded-ui-md border border-input bg-surface-muted p-0.5"
              role="group"
              aria-label="搜索范围"
            >
              <button
                type="button"
                className={`h-control-sm rounded-ui-sm px-3 text-ui-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 ${searchScope === "current" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
                aria-pressed={searchScope === "current"}
                disabled={!currentDirectoryAvailable}
                onClick={() => onSearchScopeChange("current")}
              >
                当前目录
              </button>
              <button
                type="button"
                className={`h-control-sm rounded-ui-sm px-3 text-ui-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${searchScope === "global" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
                aria-pressed={searchScope === "global"}
                onClick={() => onSearchScopeChange("global")}
              >
                全局搜索
              </button>
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="space-y-1 text-ui-xs text-muted-foreground">
              <span>类型</span>
              <Select
                className="h-control-sm"
                value={kindFilter}
                onChange={(event) => onKindFilterChange(event.target.value)}
              >
                <option value="">全部类型</option>
                {documentTypeOptions.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            </label>
            <label className="space-y-1 text-ui-xs text-muted-foreground">
              <span>状态</span>
              <Select
                className="h-control-sm"
                value={statusFilter}
                onChange={(event) => onStatusFilterChange(event.target.value)}
              >
                <option value="">全部状态</option>
                {Object.entries(statusLabel).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            </label>
            <label className="space-y-1 text-ui-xs text-muted-foreground">
              <span>来源</span>
              <Select
                className="h-control-sm"
                value={sourceFilter}
                onChange={(event) => onSourceFilterChange(event.target.value)}
              >
                <option value="">全部来源</option>
                {Object.entries(sourceLabel).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            </label>
          </div>
          <div className="mt-3 flex flex-col gap-2 border-t border-border pt-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-ui-xs text-muted-foreground" role="status">
              {activeFilterCount > 0
                ? `已启用 ${activeFilterCount} 项筛选`
                : "未启用附加筛选"}
            </p>
            <Button
              size="sm"
              variant="outline"
              onClick={onClear}
              disabled={!queryInput && activeFilterCount === 0}
            >
              清除搜索与筛选
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function TrashSearchFilters({
  queryInput,
  retentionFilter,
  retentionCounts,
  categoryFilter,
  archivedBy,
  archivedFrom,
  archivedTo,
  categories,
  onQueryInputChange,
  onRetentionFilterChange,
  onCategoryFilterChange,
  onArchivedByChange,
  onArchivedFromChange,
  onArchivedToChange,
  onClear,
}: {
  queryInput: string;
  retentionFilter: string;
  retentionCounts: Record<string, number>;
  categoryFilter: string;
  archivedBy: string;
  archivedFrom: string;
  archivedTo: string;
  categories: ManagedCategory[];
  onQueryInputChange: (value: string) => void;
  onRetentionFilterChange: (value: string) => void;
  onCategoryFilterChange: (value: string) => void;
  onArchivedByChange: (value: string) => void;
  onArchivedFromChange: (value: string) => void;
  onArchivedToChange: (value: string) => void;
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const activeFilterCount = [
    retentionFilter,
    categoryFilter,
    archivedBy,
    archivedFrom,
    archivedTo,
  ].filter(Boolean).length;
  const panelId = "trash-search-filters";

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

  const filterButtonLabel = open ? "收起回收站筛选" : "展开回收站筛选";

  return (
    <div
      ref={rootRef}
      className="relative min-w-0 w-full xl:w-72 xl:max-w-72 xl:justify-self-center min-[1400px]:w-96 min-[1400px]:max-w-96"
    >
      <div className="relative">
        <Search
          className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
        <Input
          ref={inputRef}
          className="h-control-sm pl-9 pr-11"
          value={queryInput}
          onChange={(event) => onQueryInputChange(event.target.value)}
          onFocus={() => setOpen(true)}
          aria-label="搜索回收站"
          placeholder="搜索名称、文件名、原目录或上传路径…"
        />
        <button
          type="button"
          className="absolute right-1 top-1/2 flex size-7 -translate-y-1/2 items-center justify-center rounded-ui-sm text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={filterButtonLabel}
          title={filterButtonLabel}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((current) => !current)}
        >
          <SlidersHorizontal className="size-4" aria-hidden="true" />
          {activeFilterCount > 0 && (
            <span
              className="absolute right-0.5 top-0.5 size-1.5 rounded-full bg-primary"
              aria-hidden="true"
            />
          )}
          {activeFilterCount > 0 && (
            <span className="sr-only">，已启用 {activeFilterCount} 项筛选</span>
          )}
        </button>
      </div>
      {open && (
        <div
          id={panelId}
          role="dialog"
          aria-modal="false"
          aria-label="回收站搜索筛选"
          className="fixed inset-x-4 bottom-4 top-4 z-dropdown overflow-y-auto rounded-ui-lg border border-border bg-popover p-3 text-popover-foreground shadow-overlay sm:absolute sm:inset-x-auto sm:bottom-auto sm:left-auto sm:right-0 sm:top-full sm:mt-2 sm:min-w-[36rem] sm:overflow-visible"
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-ui-xs text-muted-foreground">
              <span>保留状态</span>
              <Select
                className="h-control-sm"
                value={retentionFilter}
                onChange={(event) =>
                  onRetentionFilterChange(event.target.value)
                }
              >
                <option value="">全部状态</option>
                <option value="retained">
                  保留中（{retentionCounts.retained || 0}）
                </option>
                <option value="expiring">
                  即将到期（{retentionCounts.expiring || 0}）
                </option>
                <option value="overdue">
                  已超期（{retentionCounts.overdue || 0}）
                </option>
              </Select>
            </label>
            <CategoryCascader
              label="原目录"
              categories={categories}
              value={categoryFilter}
              onChange={onCategoryFilterChange}
            />
            <label className="space-y-1 text-ui-xs text-muted-foreground">
              <span>移入人员</span>
              <Input
                className="h-control-sm"
                value={archivedBy}
                onChange={(event) => onArchivedByChange(event.target.value)}
                placeholder="输入姓名"
              />
            </label>
            <fieldset className="grid grid-cols-2 gap-2">
              <legend className="mb-1 text-ui-xs text-muted-foreground">
                移入日期
              </legend>
              <Input
                className="h-control-sm"
                type="date"
                aria-label="移入开始日期"
                value={archivedFrom}
                onChange={(event) => onArchivedFromChange(event.target.value)}
              />
              <Input
                className="h-control-sm"
                type="date"
                aria-label="移入结束日期"
                value={archivedTo}
                onChange={(event) => onArchivedToChange(event.target.value)}
              />
            </fieldset>
          </div>
          <div className="mt-3 flex flex-col gap-2 border-t border-border pt-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-ui-xs text-muted-foreground" role="status">
              {activeFilterCount > 0
                ? `已启用 ${activeFilterCount} 项筛选`
                : "未启用附加筛选"}
            </p>
            <Button
              size="sm"
              variant="outline"
              onClick={onClear}
              disabled={!queryInput && activeFilterCount === 0}
            >
              清除搜索与筛选
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

export function AdminManagedContentPage() {
  const { state } = useAuth();
  const {
    open: openDocumentPreview,
    openXMind,
    state: previewState,
  } = usePdfPreview();
  const { open: openVideoPreview } = useVideoPlayer();
  const permissions =
    state.status === "authed" ? state.user.content_permissions || [] : [];
  const isSystemAdmin =
    state.status === "authed" && state.user.role === "admin";
  const can = (permission: ContentPermission) =>
    state.status === "authed" &&
    (state.user.role === "admin" || permissions.includes(permission));
  const [items, setItems] = useState<ManagedContentItem[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [categories, setCategories] = useState<ManagedCategory[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [uploadLimits, setUploadLimits] = useState({
    maxFileBytes: 0,
    maxBatchFiles: 0,
    maxBatchBytes: 0,
  });
  const [transcriptionSchemes, setTranscriptionSchemes] = useState<
    TranscriptionSchemeOption[]
  >([]);
  const [videoSchemeId, setVideoSchemeId] = useState("");
  const [transcriptionTargets, setTranscriptionTargets] = useState<
    ManagedContentItem[]
  >([]);
  const [transcriptionDialogOpen, setTranscriptionDialogOpen] = useState(false);
  const [transcriptionScope, setTranscriptionScope] = useState<
    "media" | "category" | "batch"
  >("media");
  const [transcriptionPreflight, setTranscriptionPreflight] = useState<Awaited<
    ReturnType<typeof adminContentApi.preflightBulkTranscription>
  > | null>(null);
  const [transcriptionRequestKey, setTranscriptionRequestKey] = useState<
    string | null
  >(null);
  const [transcriptionDialogBusy, setTranscriptionDialogBusy] = useState(false);
  const [currentFolderId, setCurrentFolderId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [uploadDefaultPublish, setUploadDefaultPublish] = useState(false);
  const [uploadPublishOverrides, setUploadPublishOverrides] = useState<
    Record<string, boolean>
  >({});
  const [pendingUploadFiles, setPendingUploadFiles] = useState<File[]>([]);
  const [pendingUploadFolderId, setPendingUploadFolderId] = useState("");
  const [pendingFolderUpload, setPendingFolderUpload] =
    useState<FolderUploadSelection | null>(null);
  const [pendingFolderUploadFolderId, setPendingFolderUploadFolderId] =
    useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadChecking, setUploadChecking] = useState(false);
  const [activeUpload, setActiveUpload] = useState<ActiveUploadState | null>(
    null,
  );
  const [lastUploadAttempt, setLastUploadAttempt] = useState<{
    batchId: string;
    files: Array<File | FolderUploadEntry>;
    categoryId: string;
    uploadMode: "files" | "folder";
    options: ManagedUploadOptions;
  } | null>(null);
  const [uploadConflictReview, setUploadConflictReview] =
    useState<UploadConflictReview | null>(null);
  const [uploadConflictChoices, setUploadConflictChoices] = useState<
    Record<number, UploadConflictChoice>
  >({});
  const [folderConflictMode, setFolderConflictMode] = useState<
    "merge" | "rename" | null
  >(null);
  const [folderConflictRenames, setFolderConflictRenames] = useState<
    Record<string, string>
  >({});
  const [uploadConflictError, setUploadConflictError] = useState<string | null>(
    null,
  );
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [selectedFolders, setSelectedFolders] = useState<string[]>([]);
  const [recursiveBulkAction, setRecursiveBulkAction] =
    useState<BulkOperationAction | null>(null);
  const [bulkAction, setBulkAction] = useState<BulkAction | null>(null);
  const [bulkFailures, setBulkFailures] = useState<
    Array<BulkManagedContentResult & { title: string }>
  >([]);
  const [bulkNote, setBulkNote] = useState("");
  const [bulkWorkbenchTargets, setBulkWorkbenchTargets] = useState<
    ManagedContentItem[]
  >([]);
  const [bulkWorkbenchResults, setBulkWorkbenchResults] = useState<
    Record<string, BulkWorkbenchResult>
  >({});
  const [bulkItemBusy, setBulkItemBusy] = useState<string | null>(null);
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const contentLoadRequestRef = useRef(0);
  const [searchScope, setSearchScope] = useState<"current" | "global">(
    "global",
  );
  const navigateToFolder = useCallback((folderId: string) => {
    setSearchScope(folderId ? "current" : "global");
    setCurrentFolderId(folderId);
  }, []);
  const [statusFilter, setStatusFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [kindFilter, setKindFilter] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [detail, setDetail] = useState<ManagedContentItem | null>(null);
  const [reviewTarget, setReviewTarget] = useState<ManagedContentItem | null>(
    null,
  );
  const [reviewDecision, setReviewDecision] = useState<"approve" | "reject">(
    "approve",
  );
  const [reviewNote, setReviewNote] = useState("");
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [publishTarget, setPublishTarget] = useState<ManagedContentItem | null>(
    null,
  );
  const [publishError, setPublishError] = useState<string | null>(null);
  const [sort, setSort] = useState<{
    key: SortKey;
    direction: SortDirection;
  } | null>(null);
  const [deleteTargets, setDeleteTargets] = useState<ManagedContentItem[]>([]);
  const [deleteAcknowledged, setDeleteAcknowledged] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [view, setViewState] = useState<ManagedContentView>(() => {
    if (typeof window === "undefined") return "library";
    const requestedView = new URLSearchParams(window.location.search).get(
      "view",
    );
    return requestedView === "uploads" ||
      requestedView === "index" ||
      requestedView === "transcription"
      ? requestedView
      : "library";
  });
  const [trashItems, setTrashItems] = useState<ManagedContentItem[]>([]);
  const [trashTotal, setTrashTotal] = useState(0);
  const [trashLoading, setTrashLoading] = useState(false);
  const [trashSelected, setTrashSelected] = useState<string[]>([]);
  const [trashRetentionFilter, setTrashRetentionFilter] = useState("");
  const [trashRetentionCounts, setTrashRetentionCounts] = useState<
    Record<string, number>
  >({});
  const [trashCategoryFilter, setTrashCategoryFilter] = useState("");
  const [trashArchivedBy, setTrashArchivedBy] = useState("");
  const [trashArchivedFrom, setTrashArchivedFrom] = useState("");
  const [trashArchivedTo, setTrashArchivedTo] = useState("");
  const [trashSort, setTrashSort] = useState<{ key: TrashSortKey; direction: SortDirection }>({ key: "archivedAt", direction: "desc" });
  const trashSortDirection = trashSort.key === "archivedAt" ? trashSort.direction : "desc";
  const [trashBulkTarget, setTrashBulkTarget] = useState("original");
  const [trashPreflight, setTrashPreflight] = useState<
    BulkRestorePreflightResult[]
  >([]);
  const [trashPreflightOpen, setTrashPreflightOpen] = useState(false);
  const [trashPurgePreflight, setTrashPurgePreflight] =
    useState<TrashPurgePreflight | null>(null);
  const [trashPurgeOpen, setTrashPurgeOpen] = useState(false);
  const [trashPurgeConfirmation, setTrashPurgeConfirmation] = useState("");
  const [trashSettingsOpen, setTrashSettingsOpen] = useState(false);
  const [trashSettings, setTrashSettings] = useState<TrashSettings | null>(
    null,
  );
  const [trashPurgeRuns, setTrashPurgeRuns] = useState<TrashPurgeRun[]>([]);
  const [restoreTarget, setRestoreTarget] = useState<ManagedContentItem | null>(
    null,
  );
  const [restoreFolderId, setRestoreFolderId] = useState("");
  const [restoreConflict, setRestoreConflict] =
    useState<FilenameConflict | null>(null);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [auditTarget, setAuditTarget] = useState<ManagedContentItem | null>(
    null,
  );
  const [auditEvents, setAuditEvents] = useState<ContentTrashAuditEvent[]>([]);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditError, setAuditError] = useState<string | null>(null);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [moveTarget, setMoveTarget] = useState<ManagedContentItem | null>(null);
  const [moveFolderId, setMoveFolderId] = useState("");
  const [moveError, setMoveError] = useState<string | null>(null);
  const [bulkMoveFolderId, setBulkMoveFolderId] = useState("");
  const [renameTarget, setRenameTarget] = useState<ManagedContentItem | null>(
    null,
  );
  const [renameTitle, setRenameTitle] = useState("");
  const [renameFilename, setRenameFilename] = useState("");
  const [renameConflict, setRenameConflict] = useState<FilenameConflict | null>(
    null,
  );
  const [renameError, setRenameError] = useState<string | null>(null);
  const [downloadTarget, setDownloadTarget] =
    useState<ManagedContentItem | null>(null);
  const [downloadPart, setDownloadPart] = useState<
    "video" | "transcript" | "all"
  >("all");
  const [mediaInfoTarget, setMediaInfoTarget] =
    useState<ManagedContentItem | null>(null);
  const [mediaInfoTitle, setMediaInfoTitle] = useState("");
  const [mediaInfoFilename, setMediaInfoFilename] = useState("");
  const [mediaInfoError, setMediaInfoError] = useState<string | null>(null);
  const [updateTarget, setUpdateTarget] = useState<ManagedContentItem | null>(
    null,
  );
  const [updateFile, setUpdateFile] = useState<File | null>(null);
  const [updateFilenameMode, setUpdateFilenameMode] = useState<"old" | "new">(
    "old",
  );
  const [updateConflict, setUpdateConflict] = useState<FilenameConflict | null>(
    null,
  );
  const [updateError, setUpdateError] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [folderScanning, setFolderScanning] = useState(false);
  const [listDropActive, setListDropActive] = useState(false);
  const [listDropPromptTop, setListDropPromptTop] = useState(96);
  const [draggedItem, setDraggedItem] = useState<ManagedContentItem | null>(
    null,
  );
  const [folderRequests, setFolderRequests] = useState<FolderRequest[]>([]);
  const [requestFolderOpen, setRequestFolderOpen] = useState(false);
  const [requestFolderName, setRequestFolderName] = useState("");
  const [folderRenameTarget, setFolderRenameTarget] =
    useState<ManagedCategory | null>(null);
  const [folderRenameName, setFolderRenameName] = useState("");
  const [folderNumberTarget, setFolderNumberTarget] =
    useState<ManagedCategory | null>(null);
  const [folderNumberValue, setFolderNumberValue] = useState("");
  const [folderNumberConfirming, setFolderNumberConfirming] = useState(false);
  const [folderDetailTarget, setFolderDetailTarget] =
    useState<ManagedCategory | null>(null);
  const [folderMoveTarget, setFolderMoveTarget] =
    useState<ManagedCategory | null>(null);
  const [folderDeleteTarget, setFolderDeleteTarget] =
    useState<ManagedCategory | null>(null);
  const [folderMoveParentId, setFolderMoveParentId] = useState("");
  const [folderActionError, setFolderActionError] = useState<string | null>(
    null,
  );
  const fileInputRef = useRef<HTMLInputElement>(null);
  const updateFileInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const accept = ".pdf,.md,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.xmind";
    if (fileInputRef.current) fileInputRef.current.accept = accept;
    if (updateFileInputRef.current) updateFileInputRef.current.accept = accept;
  });
  const folderInputRef = useRef<HTMLInputElement>(null);
  const listDragDepthRef = useRef(0);

  const setView = (nextView: ManagedContentView) => {
    setViewState(nextView);
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    if (nextView === "library") params.delete("view");
    else params.set("view", nextView);
    const queryString = params.toString();
    window.history.replaceState(
      {},
      "",
      `${window.location.pathname}${queryString ? `?${queryString}` : ""}`,
    );
  };
  useEffect(() => {
    if (view === "uploads" && !can("item.upload")) setView("library");
    if (view === "index" && !can("index.view")) setView("library");
    if (
      view === "transcription" &&
      !(state.status === "authed" && state.user.role === "admin")
    )
      setView("library");
  }, [view, state.status, permissions]);

  useEffect(() => {
    if (state.status !== "authed" || state.user.role !== "admin") return;
    try {
      void adminContentApi
        .transcriptionSchemes()
        .then((rows) => {
          const usable = rows.filter(
            (row) =>
              row.enabled && !row.archived && row.availability === "available",
          );
          setTranscriptionSchemes(rows);
          setVideoSchemeId((current) => current || usable[0]?.scheme_id || "");
        })
        .catch(() => setTranscriptionSchemes([]));
    } catch {
      setTranscriptionSchemes([]);
    }
  }, [state.status, state.status === "authed" ? state.user.role : null]);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(queryInput.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [queryInput]);
  useEffect(() => {
    setSearchScope(currentFolderId ? "current" : "global");
  }, [currentFolderId]);
  useEffect(() => {
    setPage(0);
    setSelected([]);
    setSelectedFolders([]);
  }, [
    query,
    currentFolderId,
    statusFilter,
    sourceFilter,
    kindFilter,
    searchScope,
    pageSize,
  ]);

  const hasLibrarySearchOrFilters = Boolean(
    query || statusFilter || sourceFilter || kindFilter,
  );
  const showGlobalResults =
    searchScope === "global" &&
    (Boolean(currentFolderId) || hasLibrarySearchOrFilters);

  const load = useCallback(
    async (refresh = false) => {
      const requestId = ++contentLoadRequestRef.current;
      if (refresh) setRefreshing(true);
      else if (!currentFolderId) setLoading(true);
      setError(null);
      try {
        const [capabilities, categoryRows, listing] = await Promise.all([
          adminContentApi.capabilities(),
          adminContentApi.categories(),
          adminContentApi.items({
            query: query || undefined,
            category_id:
              searchScope === "current" ? currentFolderId || undefined : undefined,
            lifecycle_status: statusFilter || undefined,
            source_origin: sourceFilter || undefined,
            doc_type: kindFilter
              ? (kindFilter as
                  | "pdf"
                  | "docx"
                  | "xlsx"
                  | "pptx"
                  | "xmind"
                  | "markdown"
                  | "transcript"
                  | "other")
              : undefined,
            sort_by: sort?.key === "docType" ? "doc_type" : undefined,
            sort_direction:
              sort?.key === "docType" ? sort.direction : undefined,
            limit: pageSize,
            offset: page * pageSize,
          }),
          ]);
        if (requestId !== contentLoadRequestRef.current) return;
        setEnabled(capabilities.enabled);
        setUploadLimits({
          maxFileBytes: capabilities.max_upload_bytes,
          maxBatchFiles: capabilities.max_batch_files,
          maxBatchBytes: capabilities.max_batch_bytes,
        });
        setCategories(categoryRows);
        const showListing = Boolean(currentFolderId) || hasLibrarySearchOrFilters;
        setItems(showListing ? listing.items : []);
        setTotal(showListing ? listing.total : 0);
        setCounts(listing.status_counts);
        setCurrentFolderId((current) =>
          current && categoryRows.some((row) => row.id === current)
            ? current
            : "",
        );
        setSelected((current) =>
          current.filter((id) =>
            listing.items.some((item) => item.version_id === id),
          ),
        );
        setSelectedFolders((current) =>
          current.filter((id) =>
            categoryRows.some((category) => category.id === id),
          ),
        );
        if (can("folder.review")) {
          const requests = await adminContentApi.folderRequests("pending");
          if (requestId === contentLoadRequestRef.current) {
            setFolderRequests(requests);
          }
        }
      } catch (loadError) {
        if (requestId !== contentLoadRequestRef.current) return;
        setError(
          loadError instanceof Error ? loadError.message : "资料加载失败",
        );
      } finally {
        if (requestId === contentLoadRequestRef.current) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [
      currentFolderId,
      page,
      pageSize,
      query,
      sourceFilter,
      statusFilter,
      kindFilter,
      searchScope,
      hasLibrarySearchOrFilters,
      sort,
    ],
  );

  useEffect(() => {
    void load();
  }, [load]);
  const hasActiveReclassification = items.some((item) =>
    ACTIVE_RECLASSIFICATION_STATUSES.has(item.reclassification_status || ""),
  );
  const hasActivePublication = items.some((item) =>
    item.lifecycle_status === "publishing"
    || ACTIVE_RECLASSIFICATION_STATUSES.has(item.latest_publication_status || ""),
  );
  useManagedContentLiveRefresh({
    active: hasActiveReclassification || hasActivePublication,
    enabled: view === "library" && !uploading,
    refresh: () => load(true),
  });

  const loadTrash = useCallback(async () => {
    if (!can("trash.view")) return;
    setTrashLoading(true);
    setError(null);
    try {
      const listing = await adminContentApi.trash({
        query: query || undefined,
        retention_status: trashRetentionFilter || undefined,
        category_id: trashCategoryFilter || undefined,
        archived_by: trashArchivedBy || undefined,
        archived_from: trashArchivedFrom
          ? Math.floor(
              new Date(`${trashArchivedFrom}T00:00:00`).getTime() / 1000,
            )
          : undefined,
        archived_to: trashArchivedTo
          ? Math.floor(new Date(`${trashArchivedTo}T23:59:59`).getTime() / 1000)
          : undefined,
        sort_direction: trashSortDirection,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      });
      setTrashItems(listing.items);
      setTrashTotal(listing.total);
      setTrashRetentionCounts(listing.retention_counts || {});
    } catch (trashFailure) {
      setError(
        trashFailure instanceof Error ? trashFailure.message : "回收站加载失败",
      );
    } finally {
      setTrashLoading(false);
    }
  }, [
    page,
    query,
    trashArchivedBy,
    trashArchivedFrom,
    trashArchivedTo,
    trashCategoryFilter,
    trashRetentionFilter,
    trashSortDirection,
  ]);

  useEffect(() => {
    if (view === "trash") void loadTrash();
  }, [loadTrash, view]);
  useEffect(() => {
    setTrashSelected([]);
  }, [
    page,
    query,
    trashArchivedBy,
    trashArchivedFrom,
    trashArchivedTo,
    trashCategoryFilter,
    trashRetentionFilter,
    trashSortDirection,
  ]);

  const upload = async (
    targetFolderId = currentFolderId,
    uploadFiles: Array<File | FolderUploadEntry> = files,
    uploadMode: "files" | "folder" = "files",
    options: ManagedUploadOptions = {},
  ) => {
    const totalBytes = uploadFiles.reduce(
      (sum, entry) => sum + ("file" in entry ? entry.file.size : entry.size),
      0,
    );
    const targetPath =
      categories.find((category) => category.id === targetFolderId)
        ?.full_path || "当前目录";
    setUploading(true);
    setActiveUpload({
      batchId: null,
      uploadMode,
      targetPath,
      totalFiles: uploadFiles.length,
      totalBytes,
      loadedBytes: 0,
      phase: "uploading",
    });
    try {
      const onProgress = (progress: ManagedUploadProgress) =>
        setActiveUpload((current) =>
          current
            ? {
                ...current,
                phase: progress.phase,
                loadedBytes:
                  progress.phase === "processing"
                    ? current.totalBytes
                    : progress.loaded,
              }
            : current,
        );
      const hasVideo = uploadFiles.some((entry) =>
        /\.mp4$/i.test("file" in entry ? entry.file.name : entry.name),
      );
      const resolvedOptions: ManagedUploadOptions = { ...options };
      if (hasVideo) {
        resolvedOptions.videoIdempotencyKeys =
          options.videoIdempotencyKeys ||
          uploadFiles.map((entry) => {
            const file = "file" in entry ? entry.file : entry;
            return /\.mp4$/i.test(file.name) ? createRequestId() : "";
          });
      }
      const result =
        uploadMode === "folder"
          ? await adminContentApi.upload(
              uploadFiles,
              targetFolderId,
              "folder",
              onProgress,
              resolvedOptions,
            )
          : await adminContentApi.upload(
              uploadFiles,
              targetFolderId,
              "files",
              onProgress,
              resolvedOptions,
            );
      const accepted = result.entries.filter(
        (entry) => entry.status === "accepted",
      ).length;
      const skipped = result.entries.length - accepted;
      setActiveUpload((current) =>
        current
          ? {
              ...current,
              batchId: result.batch_id,
              loadedBytes: current.totalBytes,
              phase: accepted ? "completed" : "failed",
              message: accepted ? undefined : "没有文件被接收，请查看任务详情",
            }
          : current,
      );
      if (skipped)
        setLastUploadAttempt({
          batchId: result.batch_id,
          files: uploadFiles,
          categoryId: targetFolderId,
          uploadMode,
          options: resolvedOptions,
        });
      else setLastUploadAttempt(null);
      if (skipped)
        toast.error(
          `已接收 ${accepted} 个文件，${skipped} 个文件未完成，请查看上传任务`,
        );
      else toast.success(`已接收 ${accepted} 个文件`);
      if (accepted) {
        setFiles([]);
        if (fileInputRef.current) fileInputRef.current.value = "";
        if (folderInputRef.current) folderInputRef.current.value = "";
        await load(true);
      }
      return accepted > 0;
    } catch (uploadError) {
      const message =
        uploadError instanceof Error ? uploadError.message : "上传失败";
      setActiveUpload((current) =>
        current ? { ...current, phase: "failed", message } : current,
      );
      toast.error(message);
    } finally {
      setUploading(false);
    }
    return false;
  };

  const resetUploadConflictReview = () => {
    setUploadConflictReview(null);
    setUploadConflictChoices({});
    setFolderConflictMode(null);
    setFolderConflictRenames({});
    setUploadConflictError(null);
  };

  const openUploadConflictReview = (review: UploadConflictReview) => {
    setUploadDialogOpen(false);
    setPendingUploadFiles([]);
    setPendingUploadFolderId("");
    setPendingFolderUpload(null);
    setPendingFolderUploadFolderId("");
    setUploadConflictReview(review);
    setUploadConflictChoices(
      Object.fromEntries(
        review.preflight.entries
          .filter(
            (entry) =>
              entry.status === "conflict" &&
              ["content_filename_conflict", "media_filename_conflict"].includes(
                entry.reason_code || "",
              ),
          )
          .map((entry) => [
            entry.sequence,
            {
              strategy: "skip" as const,
              filename: entry.suggested_filename || entry.filename,
            },
          ]),
      ),
    );
    setFolderConflictMode(
      review.preflight.folder_conflicts.length ? null : "merge",
    );
    setFolderConflictRenames(
      Object.fromEntries(
        review.preflight.folder_conflicts.map((conflict) => [
          conflict.relative_path,
          conflict.suggested_name,
        ]),
      ),
    );
    setUploadConflictError(null);
  };

  const preflightUpload = async (
    targetFolderId: string,
    uploadFiles: Array<File | FolderUploadEntry>,
    uploadMode: "files" | "folder",
    allowFolderMerge = false,
    uploadOptions: ManagedUploadOptions = {},
  ) => {
    setUploadDialogOpen(false);
    setPendingUploadFiles([]);
    setPendingUploadFolderId("");
    setPendingFolderUpload(null);
    setPendingFolderUploadFolderId("");
    setUploadChecking(true);
    setUploadConflictError(null);
    try {
      const preflight = await adminContentApi.preflightUpload(
        uploadFiles,
        targetFolderId,
        uploadMode,
        allowFolderMerge,
      );
      const hasActionableConflict =
        preflight.folder_conflicts.length > 0 ||
        preflight.entries.some((entry) => entry.status !== "ready");
      if (hasActionableConflict) {
        openUploadConflictReview({
          files: uploadFiles,
          categoryId: targetFolderId,
          uploadMode,
          allowFolderMerge,
          publishIntents: uploadOptions.publishIntents,
          preflight,
        });
        return false;
      }
      return await upload(targetFolderId, uploadFiles, uploadMode, {
        ...uploadOptions,
        allowFolderMerge,
      });
    } catch (preflightError) {
      const message =
        preflightError instanceof Error
          ? preflightError.message
          : "上传预检失败";
      setUploadConflictError(message);
      toast.error(message);
      return false;
    } finally {
      setUploadChecking(false);
    }
  };

  const refreshConflictPreflight = async (mode: "merge" | "rename") => {
    if (!uploadConflictReview) return;
    const review = uploadConflictReview;
    const rootRenames = mode === "rename" ? folderConflictRenames : {};
    const nextFiles =
      mode === "rename"
        ? renameUploadRoots(review.files, rootRenames)
        : review.files;
    setFolderConflictMode(mode);
    setUploadChecking(true);
    setUploadConflictError(null);
    try {
      const preflight = await adminContentApi.preflightUpload(
        nextFiles,
        review.categoryId,
        review.uploadMode,
        mode === "merge",
      );
      const hasRemainingConflicts =
        preflight.folder_conflicts.length > 0 ||
        preflight.entries.some((entry) => entry.status !== "ready");
      if (!hasRemainingConflicts) {
        resetUploadConflictReview();
        await upload(review.categoryId, nextFiles, review.uploadMode, {
          allowFolderMerge: mode === "merge",
          publishIntents: review.publishIntents,
        });
        return;
      }
      openUploadConflictReview({
        files: nextFiles,
        categoryId: review.categoryId,
        uploadMode: review.uploadMode,
        allowFolderMerge: mode === "merge",
        publishIntents: review.publishIntents,
        preflight,
      });
    } catch (preflightError) {
      setUploadConflictError(
        preflightError instanceof Error
          ? preflightError.message
          : "上传预检失败",
      );
    } finally {
      setUploadChecking(false);
    }
  };

  const confirmUploadConflictReview = async () => {
    if (!uploadConflictReview || uploadChecking || uploading) return;
    const review = uploadConflictReview;
    if (review.preflight.folder_conflicts.length && !folderConflictMode) {
      setUploadConflictError("请先选择同名文件夹的处理方式");
      return;
    }
    if (review.preflight.folder_conflicts.length) {
      await refreshConflictPreflight(folderConflictMode || "merge");
      return;
    }
    const keptFiles: Array<File | FolderUploadEntry> = [];
    const keptPublishIntents: boolean[] = [];
    const actions: ManagedUploadConflictAction[] = [];
    review.preflight.entries.forEach((entry, index) => {
      const choice = uploadConflictChoices[entry.sequence];
      if (entry.status === "blocked" || choice?.strategy === "skip") return;
      const file = review.files[index];
      if (!file) return;
      keptFiles.push(file);
      keptPublishIntents.push(review.publishIntents?.[index] || false);
      if (choice?.strategy === "rename") {
        actions.push({ strategy: "rename", filename: choice.filename });
      } else if (choice?.strategy === "update" && entry.conflict) {
        actions.push({
          strategy: "update",
          item_id: entry.conflict.item_id,
          expected_version_id: entry.conflict.version_id,
        });
      } else {
        actions.push({ strategy: "create" });
      }
    });
    if (!keptFiles.length) {
      toast.info("没有文件需要上传");
      resetUploadConflictReview();
      return;
    }
    resetUploadConflictReview();
    await upload(review.categoryId, keptFiles, review.uploadMode, {
      allowFolderMerge: review.allowFolderMerge,
      conflictActions: actions,
      publishIntents: keptPublishIntents,
    });
  };

  const retryUploadTask = async (task: ManagedUploadTask) => {
    if (!lastUploadAttempt || lastUploadAttempt.batchId !== task.batch_id) {
      toast.error("原始文件已不在当前页面，请重新选择后上传");
      return;
    }
    await preflightUpload(
      lastUploadAttempt.categoryId,
      lastUploadAttempt.files,
      lastUploadAttempt.uploadMode,
      lastUploadAttempt.options.allowFolderMerge,
      lastUploadAttempt.options,
    );
  };

  const prepareFileDrop = (incoming: File[]) => {
    const supportsVideo = isSystemAdmin;
    const supported = incoming.filter(
      (file) =>
        /\.(pdf|md|doc|docx|xls|xlsx|ppt|pptx|xmind)$/i.test(file.name) ||
        (supportsVideo && /\.mp4$/i.test(file.name)),
    );
    setListDropActive(false);
    if (!supported.length || !currentFolderId) {
      if (incoming.length)
        toast.error(
          supportsVideo
            ? "没有可上传的支持格式，仅支持 PDF、Markdown、Word、Excel、PPT、XMind 和 MP4 文件"
            : "没有可上传的支持格式，仅支持 PDF、Markdown、Word、Excel、PPT 和 XMind 文件",
        );
      return;
    }
    setPendingUploadFiles(supported);
    setUploadPublishOverrides({});
    setPendingUploadFolderId(currentFolderId);
    setListDropActive(false);
  };

  const confirmFileDropUpload = async () => {
    if (!pendingUploadFiles.length || !pendingUploadFolderId) return;
    const targetFolderId = pendingUploadFolderId;
    const publishIntents = pendingUploadFiles.map((file, index) =>
      /\.mp4$/i.test(file.name)
        ? false
        : (uploadPublishOverrides[uploadEntryKey(file, index)] ??
          uploadDefaultPublish),
    );
    if (
      await preflightUpload(
        targetFolderId,
        pendingUploadFiles,
        "files",
        false,
        { publishIntents },
      )
    ) {
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
    const supportsVideo = isSystemAdmin;
    const restrictedVideos = supportsVideo
      ? []
      : selection.entries.filter((entry) => /\.mp4$/i.test(entry.file.name));
    const filteredSelection: FolderUploadSelection =
      restrictedVideos.length === 0
        ? selection
        : {
            ...selection,
            entries: selection.entries.filter(
              (entry) => !/\.mp4$/i.test(entry.file.name),
            ),
            ignoredEntries: [...selection.ignoredEntries, ...restrictedVideos],
            fileCount: selection.fileCount - restrictedVideos.length,
            totalSize:
              selection.totalSize -
              restrictedVideos.reduce((sum, entry) => sum + entry.file.size, 0),
          };
    if (!filteredSelection.fileCount) {
      const ignored = filteredSelection.ignoredEntries.length;
      toast.error(
        ignored
          ? `文件夹中的 ${ignored} 个文件均不是支持的资料格式`
          : "所选文件夹中没有可上传的文件",
      );
      return;
    }
    if (filteredSelection.fileCount > uploadLimits.maxBatchFiles) {
      toast.error(`文件夹最多上传 ${uploadLimits.maxBatchFiles} 个文件`);
      return;
    }
    if (filteredSelection.totalSize > uploadLimits.maxBatchBytes) {
      toast.error(
        `文件夹总大小不能超过 ${formatUploadSize(uploadLimits.maxBatchBytes)}`,
      );
      return;
    }
    setUploadDialogOpen(false);
    setFiles([]);
    if (fileInputRef.current) fileInputRef.current.value = "";
    setPendingFolderUpload(filteredSelection);
    setUploadPublishOverrides({});
    setPendingFolderUploadFolderId(currentFolderId);
  };

  const inspectDroppedUpload = async (
    dataTransfer: DataTransfer,
    plainFileAction: "confirm" | "select" = "confirm",
  ) => {
    setFolderScanning(true);
    try {
      const dropped = await collectDroppedUpload(dataTransfer);
      if (dropped.mode === "folder") prepareFolderSelection(dropped.selection);
      else if (plainFileAction === "select") acceptFiles(dropped.files);
      else prepareFileDrop(dropped.files);
    } catch (scanError) {
      toast.error(
        scanError instanceof Error
          ? scanError.message
          : "读取文件夹失败，请重新选择",
      );
    } finally {
      setFolderScanning(false);
    }
  };

  const selectFolder = (incoming: File[]) => {
    setFolderScanning(true);
    try {
      prepareFolderSelection(folderSelectionFromFiles(incoming));
    } catch (scanError) {
      toast.error(
        scanError instanceof Error
          ? scanError.message
          : "读取文件夹失败，请重新选择",
      );
    } finally {
      setFolderScanning(false);
      if (folderInputRef.current) folderInputRef.current.value = "";
    }
  };

  const confirmFolderUpload = async () => {
    if (!pendingFolderUpload?.entries.length || !pendingFolderUploadFolderId)
      return;
    const publishIntents = pendingFolderUpload.entries.map((entry, index) =>
      /\.mp4$/i.test(entry.file.name)
        ? false
        : (uploadPublishOverrides[uploadEntryKey(entry, index)] ??
          uploadDefaultPublish),
    );
    if (
      await preflightUpload(
        pendingFolderUploadFolderId,
        pendingFolderUpload.entries,
        "folder",
        false,
        { publishIntents },
      )
    ) {
      setPendingFolderUpload(null);
      setPendingFolderUploadFolderId("");
    }
  };

  const currentFolder =
    categories.find((category) => category.id === currentFolderId) || null;
  const currentFolderDropLabel = currentFolder
    ? `${currentFolder.display_code} ${currentFolder.display_name}`.trim()
    : "当前目录";
  const listDropEnabled =
    enabled &&
    can("item.upload") &&
    Boolean(currentFolderId) &&
    !uploading &&
    !uploadChecking &&
    !folderScanning;
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
    const promptTop = Math.round(
      Math.min(
        Math.max(edgeInset, bounds.height - edgeInset),
        Math.max(edgeInset, visibleCenter),
      ),
    );
    setListDropPromptTop((current) =>
      current === promptTop ? current : promptTop,
    );
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
    const hasDirectory = Array.from(dataTransfer.items || []).some(
      (item) =>
        (
          item as DataTransferItem & {
            webkitGetAsEntry?: () => { isDirectory?: boolean } | null;
          }
        ).webkitGetAsEntry?.()?.isDirectory,
    );
    if (!hasDirectory) {
      prepareFileDrop(Array.from(dataTransfer.files || []));
      return;
    }
    void inspectDroppedUpload(dataTransfer);
  };
  const childFolders = useMemo(
    () =>
      categories.filter(
        (category) =>
          category.parent_id === (currentFolderId || null) &&
          category.is_active,
      ),
    [categories, currentFolderId],
  );
  const breadcrumbs = useMemo(() => {
    const result: ManagedCategory[] = [];
    let cursor = currentFolder;
    while (cursor) {
      result.unshift(cursor);
      cursor =
        categories.find((category) => category.id === cursor?.parent_id) ||
        null;
    }
    return result;
  }, [categories, currentFolder]);
  const currentRootFolderId = breadcrumbs[0]?.id || "";
  const sortedChildFolders = useMemo(() => {
    const visibleFolders = showGlobalResults ? [] : childFolders;
    if (sort?.key !== "title")
      return [...visibleFolders].sort(compareManagedCategories);
    return [...visibleFolders].sort((left, right) => {
      const comparison =
        `${left.display_code} ${left.display_name}`.localeCompare(
          `${right.display_code} ${right.display_name}`,
          "zh-CN",
          { numeric: true, sensitivity: "base" },
        );
      return sort.direction === "asc" ? comparison : -comparison;
    });
  }, [childFolders, showGlobalResults, sort]);
  const folderRenameConflict = useMemo(() => {
    if (!folderRenameTarget) return null;
    const key = normalizeFolderName(folderRenameName);
    if (!key) return null;
    return (
      categories.find(
        (category) =>
          category.id !== folderRenameTarget.id &&
          category.parent_id === folderRenameTarget.parent_id &&
          normalizeFolderName(category.display_name) === key,
      ) || null
    );
  }, [categories, folderRenameName, folderRenameTarget]);
  const folderNumberSiblings = useMemo(
    () =>
      folderNumberTarget
        ? categories
            .filter(
              (category) => category.parent_id === folderNumberTarget.parent_id,
            )
            .sort(compareManagedCategories)
        : [],
    [categories, folderNumberTarget],
  );
  const parsedFolderNumber = Number(folderNumberValue);
  const folderNumberValid =
    Number.isInteger(parsedFolderNumber) &&
    parsedFolderNumber >= 1 &&
    parsedFolderNumber <= folderNumberSiblings.length;
  const currentFolderNumber = folderNumberTarget
    ? folderNumberSiblings.findIndex(
        (category) => category.id === folderNumberTarget.id,
      ) + 1
    : 0;
  const folderNumberConflict = folderNumberValid
    ? folderNumberSiblings[parsedFolderNumber - 1]
    : null;
  const folderMoveConstraints = useMemo(() => {
    const reasons: Record<string, string> = {};
    if (!folderMoveTarget) return { reasons, rootReason: "" };
    const descendants = new Set<string>();
    const visit = (parentId: string) =>
      categories
        .filter((category) => category.parent_id === parentId)
        .forEach((child) => {
          descendants.add(child.id);
          visit(child.id);
        });
    visit(folderMoveTarget.id);
    const destinationConflict = (parentId: string | null) => {
      const siblings = categories.filter(
        (category) =>
          category.id !== folderMoveTarget.id &&
          category.parent_id === parentId,
      );
      if (
        siblings.some(
          (category) =>
            normalizeFolderName(category.display_name) ===
            normalizeFolderName(folderMoveTarget.display_name),
        )
      ) {
        return `目标目录下已存在同名文件夹“${folderMoveTarget.display_name}”`;
      }
      return "";
    };
    categories.forEach((category) => {
      if (category.id === folderMoveTarget.id)
        reasons[category.id] = "不能移动到文件夹自身";
      else if (descendants.has(category.id))
        reasons[category.id] = "不能移动到自身的子目录";
      else if (category.id === folderMoveTarget.parent_id)
        reasons[category.id] = "文件夹已经位于此目录";
      else reasons[category.id] = destinationConflict(category.id);
    });
    const rootReason =
      folderMoveTarget.parent_id === null
        ? "文件夹已经位于根目录"
        : destinationConflict(null);
    return { reasons, rootReason };
  }, [categories, folderMoveTarget]);
  const sortedItems = useMemo(() => {
    if (!sort) return items;
    if (sort.key === "docType") return items;
    return [...items].sort((left, right) => {
      let comparison: number;
      switch (sort.key) {
        case "updatedAt":
          comparison = (left.updated_at || 0) - (right.updated_at || 0);
          break;
        case "status":
          comparison = (
            statusLabel[left.lifecycle_status] || left.lifecycle_status
          ).localeCompare(
            statusLabel[right.lifecycle_status] || right.lifecycle_status,
            "zh-CN",
            { numeric: true, sensitivity: "base" },
          );
          break;
        case "source":
          comparison = (
            sourceLabel[left.source_origin] || left.source_origin
          ).localeCompare(
            sourceLabel[right.source_origin] || right.source_origin,
            "zh-CN",
            { numeric: true, sensitivity: "base" },
          );
          break;
        default:
          comparison = left.title.localeCompare(right.title, "zh-CN", {
            numeric: true,
            sensitivity: "base",
          });
      }
      return sort.direction === "asc" ? comparison : -comparison;
    });
  }, [items, sort]);
  const toggleSort = (key: SortKey) =>
    setSort((current) =>
      current?.key === key
        ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
        : { key, direction: "asc" },
    );
  const sortIcon = (key: SortKey) =>
    sort?.key !== key ? (
      <ArrowUpDown className="size-3.5" />
    ) : sort.direction === "asc" ? (
      <ArrowUp className="size-3.5" />
    ) : (
      <ArrowDown className="size-3.5" />
    );

  const createFolder = async () => {
    if (!currentFolder || !newFolderName.trim()) return;
    setBusyAction("new-folder");
    try {
      const siblingNumber = childFolders.length + 1;
      await adminContentApi.createCategory({
        parent_id: currentFolder.id,
        display_code: String(siblingNumber).padStart(2, "0"),
        display_name: newFolderName.trim(),
        sort_order: siblingNumber * 10,
        target_position: siblingNumber,
        confirm_number_shift: true,
      });
      setNewFolderName("");
      setNewFolderOpen(false);
      toast.success("文件夹已创建");
      await load(true);
    } catch (folderError) {
      toast.error(
        folderError instanceof Error ? folderError.message : "创建文件夹失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const openFolderRename = (folder: ManagedCategory) => {
    setFolderRenameTarget(folder);
    setFolderRenameName(folder.display_name);
    setFolderActionError(null);
  };

  const saveFolderRename = async () => {
    if (!folderRenameTarget || !folderRenameName.trim() || folderRenameConflict)
      return;
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
      setFolderActionError(
        renameError instanceof Error ? renameError.message : "重命名文件夹失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const openFolderNumber = (folder: ManagedCategory) => {
    const siblings = categories
      .filter((category) => category.parent_id === folder.parent_id)
      .sort(compareManagedCategories);
    setFolderNumberTarget(folder);
    setFolderNumberValue(
      String(siblings.findIndex((category) => category.id === folder.id) + 1),
    );
    setFolderNumberConfirming(false);
    setFolderActionError(null);
  };

  const saveFolderNumber = async () => {
    if (
      !folderNumberTarget ||
      !folderNumberValid ||
      parsedFolderNumber === currentFolderNumber
    )
      return;
    setBusyAction(`folder:${folderNumberTarget.id}:number`);
    setFolderActionError(null);
    try {
      await adminContentApi.updateCategoryNumber(folderNumberTarget.id, {
        target_position: parsedFolderNumber,
        confirm_number_shift: true,
        expected_version: folderNumberTarget.version,
      });
      toast.success(
        `文件夹编号已调整为 ${String(parsedFolderNumber).padStart(2, "0")}`,
      );
      setFolderNumberTarget(null);
      setFolderNumberConfirming(false);
      await load(true);
    } catch (numberError) {
      setFolderNumberConfirming(false);
      setFolderActionError(
        numberError instanceof Error
          ? numberError.message
          : "调整文件夹编号失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const openFolderMove = (folder: ManagedCategory) => {
    setFolderMoveTarget(folder);
    setFolderMoveParentId("");
    setFolderActionError(null);
  };

  const categoryDeleted = async (
    result: Awaited<ReturnType<typeof adminContentApi.deleteCategory>>,
  ) => {
    setCategories(result.categories);
    setFolderDeleteTarget(null);
    if (
      currentFolderId &&
      !result.categories.some((category) => category.id === currentFolderId)
    ) {
      navigateToFolder(result.parent_id || "");
    }
    if (result.force_delete) {
      if (result.cleanup_status === "partial") {
        toast.error(
          `目录已移除，但有 ${result.cleanup_error_count} 项关联数据清理失败，请联系系统管理员`,
        );
      } else
        toast.success(
          `已永久删除 ${result.deleted_item_count} 份资料和 ${result.deleted_folder_count} 个文件夹`,
        );
    } else
      toast.success(
        result.deleted_folder_count > 1
          ? "已删除 " + result.deleted_folder_count + " 个文件夹"
          : "文件夹已删除",
      );
    await load(true);
  };

  const saveFolderMove = async () => {
    if (!folderMoveTarget || !folderMoveParentId) return;
    const disabledReason =
      folderMoveParentId === ROOT_FOLDER_VALUE
        ? folderMoveConstraints.rootReason
        : folderMoveConstraints.reasons[folderMoveParentId];
    if (disabledReason) return;
    setBusyAction(`folder:${folderMoveTarget.id}:move`);
    setFolderActionError(null);
    try {
      await adminContentApi.moveCategory(folderMoveTarget.id, {
        target_parent_id:
          folderMoveParentId === ROOT_FOLDER_VALUE ? null : folderMoveParentId,
        before_category_id: null,
        expected_version: folderMoveTarget.version,
      });
      toast.success(`已移动文件夹“${folderMoveTarget.display_name}”`);
      setFolderMoveTarget(null);
      setFolderMoveParentId("");
      await load(true);
    } catch (moveFailure) {
      setFolderActionError(
        moveFailure instanceof Error ? moveFailure.message : "移动文件夹失败",
      );
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
        await adminContentApi.reclassify(
          moveTarget.item_id,
          moveFolderId,
          moveTarget.version_id,
        );
        toast.success("分类调整任务已提交");
      } else {
        await adminContentApi.move(
          moveTarget.item_id,
          moveFolderId,
          moveTarget.version_id,
        );
        toast.success(
          mode === "archive" ? "归档目录已调整" : `已移动“${moveTarget.title}”`,
        );
      }
      setMoveTarget(null);
      setMoveFolderId("");
      await load(true);
    } catch (moveFailure) {
      setMoveError(
        moveFailure instanceof Error ? moveFailure.message : "调整目录失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const moveItemTo = async (
    item: ManagedContentItem,
    targetFolderId: string,
  ) => {
    if (item.category_id === targetFolderId) return;
    setBusyAction(`${item.version_id}:move`);
    try {
      const mode = moveOperation(item);
      if (mode === "reclassify")
        await adminContentApi.reclassify(
          item.item_id,
          targetFolderId,
          item.version_id,
        );
      else
        await adminContentApi.move(
          item.item_id,
          targetFolderId,
          item.version_id,
        );
      toast.success(
        mode === "archive"
          ? "归档目录已调整"
          : mode === "reclassify"
            ? "分类调整任务已提交"
            : `已移动“${item.title}”`,
      );
      setDraggedItem(null);
      await load(true);
    } catch (moveError) {
      toast.error(
        moveError instanceof Error ? moveError.message : "调整目录失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const requestFolder = async () => {
    if (!currentFolder || !requestFolderName.trim()) return;
    setBusyAction("request-folder");
    try {
      await adminContentApi.createFolderRequest(
        currentFolder.id,
        requestFolderName.trim(),
      );
      setRequestFolderName("");
      setRequestFolderOpen(false);
      toast.success("目录申请已提交");
    } catch (requestError) {
      toast.error(
        requestError instanceof Error
          ? requestError.message
          : "提交目录申请失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const reviewFolder = async (request: FolderRequest, approved: boolean) => {
    setBusyAction(`folder-request:${request.id}`);
    try {
      await adminContentApi.reviewFolderRequest(request.id, approved);
      toast.success(approved ? "目录申请已批准" : "目录申请已退回");
      await load(true);
    } catch (reviewError) {
      toast.error(
        reviewError instanceof Error ? reviewError.message : "处理目录申请失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const acceptFiles = (incoming: File[]) => {
    const supportsVideo = isSystemAdmin;
    const supported = incoming.filter(
      (file) =>
        /\.(pdf|md|doc|docx|xls|xlsx|ppt|pptx|xmind)$/i.test(file.name) ||
        (supportsVideo && /\.mp4$/i.test(file.name)),
    );
    const oversized = supported.filter(
      (file) => file.size > uploadLimits.maxFileBytes,
    );
    const kept = supported.filter(
      (file) => file.size <= uploadLimits.maxFileBytes,
    );
    setFiles(kept);
    setUploadPublishOverrides({});
    if (supported.length !== incoming.length)
      toast.error("已忽略不支持的文件格式");
    if (oversized.length)
      toast.error(
        `单文件不能超过 ${formatUploadSize(uploadLimits.maxFileBytes)}`,
      );
  };

  const openUploadDialog = () => {
    setFiles([]);
    setUploadPublishOverrides({});
    setDragActive(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
    setUploadDialogOpen(true);
  };

  const closeUploadDialog = () => {
    if (uploading) return;
    setUploadDialogOpen(false);
    setFiles([]);
    setUploadPublishOverrides({});
    setDragActive(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const confirmDialogUpload = async () => {
    if (!files.length || !currentFolderId) return;
    const targetFolderId = currentFolderId;
    const publishIntents = files.map((file, index) =>
      /\.mp4$/i.test(file.name)
        ? false
        : (uploadPublishOverrides[uploadEntryKey(file, index)] ??
          uploadDefaultPublish),
    );
    if (
      await preflightUpload(targetFolderId, files, "files", false, {
        publishIntents,
      })
    )
      setUploadDialogOpen(false);
  };

  const act = async (
    item: ManagedContentItem,
    action: string,
    operation: () => Promise<unknown>,
    success: string,
  ) => {
    setBusyAction(`${item.version_id}:${action}`);
    try {
      await operation();
      setDetail(null);
      toast.success(success);
      await load(true);
    } catch (actionError) {
      toast.error(
        actionError instanceof Error ? actionError.message : "操作失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const regeneratePreview = async (item: ManagedContentItem) => {
    const actionKey = `${item.version_id}:preview`;
    if (busyAction) return;
    setBusyAction(actionKey);
    try {
      const result = await adminContentApi.regeneratePreview(item.version_id);
      const applyReadyPreview = (candidate: ManagedContentItem) =>
        candidate.version_id === item.version_id
          ? {
              ...candidate,
              preview_parent_id: result.preview_parent_id,
              preview_status: "ready" as const,
            }
          : candidate;
      setItems((current) => current.map(applyReadyPreview));
      setDetail((current) => (current ? applyReadyPreview(current) : current));
      setReviewTarget((current) =>
        current ? applyReadyPreview(current) : current,
      );
      toast.success("PPTX 预览已生成");
    } catch (previewError) {
      toast.error(
        previewError instanceof Error
          ? previewError.message
          : "PPTX 预览生成失败，请稍后重试",
      );
    } finally {
      setBusyAction(null);
    }
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
      await adminContentApi.review(
        reviewTarget.version_id,
        approved,
        reviewNote,
      );
      setReviewTarget(null);
      toast.success(approved ? "资料已确认" : "资料已退回");
      await load(true);
    } catch (actionError) {
      setReviewError(
        actionError instanceof Error ? actionError.message : "审核失败，请重试",
      );
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
      setPublishError(
        actionError instanceof Error ? actionError.message : "发布失败，请重试",
      );
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
      const result =
        targets.length === 1
          ? {
              results: [
                {
                  version_id: targets[0].version_id,
                  status: "succeeded" as const,
                  message: null,
                },
              ],
              succeeded: 1,
              failed: 0,
            }
          : await adminContentApi.bulkArchive(
              targets.map((item) => ({
                item_id: item.item_id,
                expected_version_id: item.version_id,
              })),
            );
      if (targets.length === 1)
        await adminContentApi.archive(
          targets[0].item_id,
          targets[0].version_id,
        );
      const failures = result.results.filter(
        (entry) => entry.status === "failed",
      );
      setSelected(failures.map((entry) => entry.version_id));
      if (failures.length) {
        const failedVersionIds = new Set(
          failures.map((entry) => entry.version_id),
        );
        setDeleteTargets(
          targets.filter((item) => failedVersionIds.has(item.version_id)),
        );
        setDeleteError(
          `成功 ${result.succeeded} 份，失败 ${result.failed} 份：${failures.map((entry) => entry.message || "请刷新后重试").join("；")}`,
        );
      } else {
        setDeleteTargets([]);
        toast.success(
          targets.length === 1
            ? `已将“${targets[0].title}”移至回收站`
            : `已将 ${result.succeeded} 份资料移至回收站`,
        );
      }
      await load(true);
    } catch (deleteFailure) {
      setDeleteError(
        deleteFailure instanceof Error
          ? deleteFailure.message
          : "移入回收站失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const openRenameDialog = (item: ManagedContentItem) => {
    setRenameTarget(item);
    setRenameTitle(item.title);
    setRenameFilename(item.original_filename);
    setRenameConflict(null);
    setRenameError(null);
  };

  const renameContent = async (replace = false) => {
    if (!renameTarget) return;
    setBusyAction("rename");
    setRenameError(null);
    try {
      await adminContentApi.rename(renameTarget.item_id, {
        title: renameTitle.trim(),
        original_filename: renameFilename.trim(),
        expected_version_id: renameTarget.version_id,
        ...(replace && renameConflict
          ? {
              replace_conflict_item_id: renameConflict.item_id,
              replace_conflict_expected_version_id: renameConflict.version_id,
            }
          : {}),
      });
      setRenameTarget(null);
      setRenameConflict(null);
      toast.success("已创建重命名草稿，请重新提交确认并发布");
      await load(true);
    } catch (renameFailure) {
      const conflict = filenameConflictFrom(renameFailure);
      if (conflict) setRenameConflict(conflict);
      else
        setRenameError(
          renameFailure instanceof Error ? renameFailure.message : "重命名失败",
        );
    } finally {
      setBusyAction(null);
    }
  };

  const openUpdateDialog = (item: ManagedContentItem) => {
    setUpdateTarget(item);
    setUpdateFile(null);
    setUpdateFilenameMode("old");
    setUpdateConflict(null);
    setUpdateError(null);
    if (updateFileInputRef.current) updateFileInputRef.current.value = "";
  };

  const updateContent = async (replace = false) => {
    if (!updateTarget || !updateFile) return;
    setBusyAction("update");
    setUpdateError(null);
    try {
      await adminContentApi.updateVersion(
        updateTarget.item_id,
        updateFile,
        updateTarget.version_id,
        updateFilenameMode,
        replace && updateConflict
          ? {
              item_id: updateConflict.item_id,
              version_id: updateConflict.version_id,
            }
          : undefined,
      );
      setUpdateTarget(null);
      setUpdateConflict(null);
      setUpdateFile(null);
      toast.success("已创建更新草稿，请重新提交确认并发布");
      await load(true);
    } catch (updateFailure) {
      const conflict = filenameConflictFrom(updateFailure);
      if (conflict) setUpdateConflict(conflict);
      else
        setUpdateError(
          updateFailure instanceof Error
            ? updateFailure.message
            : "更新资料失败",
        );
    } finally {
      setBusyAction(null);
    }
  };

  const downloadContent = async (item: ManagedContentItem) => {
    setBusyAction(`${item.version_id}:download`);
    try {
      const result = await adminContentApi.downloadFile(
        item.version_id,
        item.original_filename,
      );
      triggerManagedDownload(result.blob, result.filename);
      toast.success(`已开始下载“${item.title}”`);
    } catch (downloadError) {
      toast.error(
        downloadError instanceof Error ? downloadError.message : "下载资料失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const openMediaDownload = (item: ManagedContentItem) => {
    setDownloadTarget(item);
    setDownloadPart("all");
  };

  const downloadMedia = async () => {
    if (!downloadTarget) return;
    const fallbackFilename =
      downloadPart === "video"
        ? downloadTarget.original_filename
        : downloadPart === "transcript"
          ? `${downloadTarget.title}-转录稿.md`
          : `${downloadTarget.title}-视频资料.zip`;
    setBusyAction(`${downloadTarget.version_id}:download`);
    try {
      const result = await adminContentApi.downloadMedia(
        downloadTarget.item_id,
        downloadPart,
        fallbackFilename,
      );
      triggerManagedDownload(result.blob, result.filename);
      toast.success(`已开始下载“${downloadTarget.title}”`);
      setDownloadTarget(null);
    } catch (downloadError) {
      toast.error(
        downloadError instanceof Error
          ? downloadError.message
          : "下载视频资料失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const openMediaInfoDialog = (item: ManagedContentItem) => {
    setMediaInfoTarget(item);
    setMediaInfoTitle(item.title);
    setMediaInfoFilename(item.original_filename);
    setMediaInfoError(null);
  };

  const createMediaInfoRevision = async () => {
    if (!mediaInfoTarget?.media_id) return;
    setBusyAction("media-metadata");
    setMediaInfoError(null);
    try {
      await adminContentApi.createMediaMetadataRevision(
        mediaInfoTarget.media_id,
        mediaInfoTarget.version_id,
        mediaInfoTitle.trim(),
        mediaInfoFilename.trim(),
        createRequestId(),
      );
      setMediaInfoTarget(null);
      toast.success("媒体信息修订已创建，请在转写工作台审核并发布");
      await load(true);
    } catch (metadataError) {
      setMediaInfoError(
        metadataError instanceof Error
          ? metadataError.message
          : "媒体信息修订创建失败",
      );
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
      const result = await adminContentApi.bulkDownload(
        selectedItems.map((item) => item.version_id),
      );
      triggerManagedDownload(result.blob, result.filename);
      toast.success(`已打包 ${selectedCount} 份资料并开始下载`, {
        id: BULK_DOWNLOAD_TOAST_ID,
        duration: 4000,
      });
    } catch (downloadError) {
      toast.error(
        downloadError instanceof Error ? downloadError.message : "批量下载失败",
        { id: BULK_DOWNLOAD_TOAST_ID, duration: 5000 },
      );
    } finally {
      setBusyAction(null);
    }
  };

  const downloadFolder = async (folder: ManagedCategory) => {
    if (!can("item.download")) return;
    const folderLabel = `${folder.display_code} ${folder.display_name}`;
    const totalCount = folder.total_item_count ?? folder.item_count;
    setBusyAction(`folder:${folder.id}:download`);
    toast.info(`正在打包 ${totalCount} 份资料，请稍候…`, {
      id: BULK_DOWNLOAD_TOAST_ID,
      description: "文件较多时可能需要几秒。",
      duration: Infinity,
    });
    try {
      const result = await adminContentApi.downloadCategory(
        folder.id,
        `${folderLabel}-资料打包下载.zip`,
      );
      triggerManagedDownload(result.blob, result.filename);
      toast.success(`已打包“${folderLabel}”并开始下载`, {
        id: BULK_DOWNLOAD_TOAST_ID,
        duration: 4000,
      });
    } catch (downloadError) {
      toast.error(
        downloadError instanceof Error
          ? downloadError.message
          : "文件夹打包下载失败",
        { id: BULK_DOWNLOAD_TOAST_ID, duration: 5000 },
      );
    } finally {
      setBusyAction(null);
    }
  };

  const openRestore = (target: ManagedContentItem) => {
    setRestoreTarget(target);
    setRestoreFolderId(
      categories.some((category) => category.id === target.category_id)
        ? target.category_id
        : "",
    );
    setRestoreConflict(null);
    setRestoreError(null);
  };

  const restoreContent = async (replaceConflict = false) => {
    if (!restoreTarget) return;
    const target = restoreTarget;
    setBusyAction(`${target.version_id}:restore`);
    setRestoreError(null);
    try {
      const result = await adminContentApi.restore(
        target.item_id,
        target.version_id,
        {
          target_category_id: restoreFolderId,
          ...(replaceConflict && restoreConflict
            ? {
                replace_conflict_item_id: restoreConflict.item_id,
                replace_conflict_expected_version_id:
                  restoreConflict.version_id,
              }
            : {}),
        },
      );
      setRestoreTarget(null);
      setRestoreConflict(null);
      toast.success(
        result.replaced_conflict
          ? `已替换同名资料并恢复“${target.title}”`
          : result.moved_to_alternate_category
            ? `已恢复“${target.title}”到所选目录`
            : `已恢复“${target.title}”`,
      );
      await loadTrash();
    } catch (restoreFailure) {
      const conflict = filenameConflictFrom(restoreFailure);
      if (conflict) setRestoreConflict(conflict);
      else
        setRestoreError(
          restoreFailure instanceof Error
            ? restoreFailure.message
            : "恢复资料失败",
        );
    } finally {
      setBusyAction(null);
    }
  };

  const bulkRestoreTrash = async () => {
    const readyIds = new Set(
      trashPreflight
        .filter((entry) => entry.status === "ready")
        .map((entry) => entry.version_id),
    );
    const targets = trashItems.filter(
      (item) =>
        trashSelected.includes(item.version_id) &&
        readyIds.has(item.version_id),
    );
    if (targets.length === 0) return;
    setBusyAction("bulk-restore");
    try {
      const result = await adminContentApi.bulkRestore(
        targets.map((item) => ({
          item_id: item.item_id,
          expected_version_id: item.version_id,
        })),
        trashBulkTarget === "original" ? undefined : trashBulkTarget,
      );
      const failedIds = result.results
        .filter((entry) => entry.status === "failed")
        .map((entry) => entry.version_id);
      setTrashSelected(failedIds);
      setTrashPreflightOpen(false);
      setTrashPreflight([]);
      setTrashBulkTarget("original");
      if (result.failed)
        toast.error(
          `已恢复 ${result.succeeded} 份，${result.failed} 份因同名冲突或状态变化仍保留在回收站`,
        );
      else toast.success(`已恢复 ${result.succeeded} 份资料`);
      await loadTrash();
    } catch (bulkRestoreError) {
      toast.error(
        bulkRestoreError instanceof Error
          ? bulkRestoreError.message
          : "批量恢复失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const preflightBulkRestore = async () => {
    const targets = trashItems.filter((item) =>
      trashSelected.includes(item.version_id),
    );
    if (targets.length === 0) return;
    setBusyAction("restore-preflight");
    try {
      const result = await adminContentApi.preflightBulkRestore(
        targets.map((item) => ({
          item_id: item.item_id,
          expected_version_id: item.version_id,
        })),
        trashBulkTarget === "original" ? undefined : trashBulkTarget,
      );
      setTrashPreflight(result.results);
      setTrashPreflightOpen(true);
    } catch (failure) {
      toast.error(failure instanceof Error ? failure.message : "恢复预检失败");
    } finally {
      setBusyAction(null);
    }
  };

  const openTrashPurge = async () => {
    const targets = trashItems.filter((item) =>
      trashSelected.includes(item.version_id),
    );
    if (targets.length === 0) return;
    setBusyAction("purge-preflight");
    try {
      const result = await adminContentApi.preflightTrashPurge(
        targets.map((item) => ({
          item_id: item.item_id,
          expected_version_id: item.version_id,
        })),
      );
      setTrashPurgePreflight(result);
      setTrashPurgeConfirmation("");
      setTrashPurgeOpen(true);
    } catch (purgeError) {
      toast.error(
        purgeError instanceof Error ? purgeError.message : "永久删除检查失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const confirmTrashPurge = async () => {
    if (!trashPurgePreflight) return;
    const targets = trashPurgePreflight.items.filter(
      (item) => item.status === "ready",
    );
    setBusyAction("trash-purge");
    try {
      const result = await adminContentApi.purgeTrash(
        targets.map((item) => ({
          item_id: item.item_id,
          expected_version_id: item.version_id,
        })),
        trashPurgeConfirmation,
      );
      setTrashPurgeOpen(false);
      setTrashPurgePreflight(null);
      setTrashSelected([]);
      if (result.failed_count)
        toast.error(
          `已永久删除 ${result.succeeded_count} 份，${result.failed_count} 份失败`,
        );
      else toast.success(`已永久删除 ${result.succeeded_count} 份资料`);
      await loadTrash();
    } catch (purgeError) {
      toast.error(
        purgeError instanceof Error ? purgeError.message : "永久删除失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const previewOverdueTrashPurge = async () => {
    setBusyAction("purge-overdue-preview");
    try {
      const result = await adminContentApi.previewOverdueTrashPurge();
      if (result.ready_count === 0) {
        toast.info("当前没有可清理的已超期资料");
        return;
      }
      setTrashPurgePreflight(result);
      setTrashPurgeConfirmation("");
      setTrashPurgeOpen(true);
    } catch (purgeError) {
      toast.error(
        purgeError instanceof Error ? purgeError.message : "已超期资料检查失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const openTrashSettings = async () => {
    setBusyAction("trash-settings");
    try {
      const [settings, runs] = await Promise.all([
        adminContentApi.trashSettings(),
        adminContentApi.trashPurgeRuns(),
      ]);
      setTrashSettings(settings);
      setTrashPurgeRuns(runs);
      setTrashSettingsOpen(true);
    } catch (settingsError) {
      toast.error(
        settingsError instanceof Error
          ? settingsError.message
          : "清理策略加载失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const saveTrashSettings = async () => {
    if (!trashSettings) return;
    setBusyAction("trash-settings-save");
    try {
      const saved = await adminContentApi.updateTrashSettings({
        cleanup_enabled: trashSettings.cleanup_enabled,
        retention_days: trashSettings.retention_days,
        warning_days: trashSettings.warning_days,
        batch_limit: trashSettings.batch_limit,
      });
      setTrashSettings(saved);
      setTrashSettingsOpen(false);
      toast.success(
        saved.cleanup_enabled
          ? "自动清理策略已启用"
          : "清理策略已保存，自动清理保持关闭",
      );
      await loadTrash();
    } catch (settingsError) {
      toast.error(
        settingsError instanceof Error
          ? settingsError.message
          : "清理策略保存失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const exportTrash = async () => {
    setBusyAction("trash-export");
    try {
      const result = await adminContentApi.exportTrash({
        query,
        retention_status: trashRetentionFilter || null,
        category_id: trashCategoryFilter || null,
        archived_by: trashArchivedBy,
        archived_from: trashArchivedFrom
          ? Math.floor(
              new Date(`${trashArchivedFrom}T00:00:00`).getTime() / 1000,
            )
          : null,
        archived_to: trashArchivedTo
          ? Math.floor(new Date(`${trashArchivedTo}T23:59:59`).getTime() / 1000)
          : null,
        sort_direction: trashSortDirection,
      });
      triggerManagedDownload(result.blob, result.filename);
      toast.success("回收站处置清单已导出");
    } catch (failure) {
      toast.error(
        failure instanceof Error ? failure.message : "导出处置清单失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const openAudit = async (target: ManagedContentItem) => {
    setAuditTarget(target);
    setAuditEvents([]);
    setAuditError(null);
    setAuditLoading(true);
    try {
      setAuditEvents(await adminContentApi.auditEvents(target.item_id));
    } catch (auditFailure) {
      setAuditError(
        auditFailure instanceof Error
          ? auditFailure.message
          : "操作记录加载失败",
      );
    } finally {
      setAuditLoading(false);
    }
  };

  const selectedItems = useMemo(
    () => items.filter((item) => selected.includes(item.version_id)),
    [items, selected],
  );

  const canMoveItem = (item: ManagedContentItem) => {
    if (
      ACTIVE_RECLASSIFICATION_STATUSES.has(item.reclassification_status || "")
    )
      return false;
    if (item.content_kind === "media_transcript") return can("item.publish");
    if (item.has_published_head) {
      return (
        can("item.reclassify_published") &&
        item.is_current &&
        item.lifecycle_status === "published"
      );
    }
    return (
      (can("item.move_draft") &&
        ["draft", "rejected"].includes(item.lifecycle_status)) ||
      (item.lifecycle_status === "awaiting_review")
    );
  };
  const canStartTranscription = (item: ManagedContentItem) =>
    isSystemAdmin &&
    item.content_kind === "media_transcript" &&
    Boolean(item.media_id) &&
    (item.lifecycle_status === "awaiting_transcription" ||
      (item.lifecycle_status === "transcription_failed" &&
        Boolean(item.transcription_job_id) &&
        item.transcription_failure_classification !== "permanent"));
  const canSelectItem = (item: ManagedContentItem) =>
    item.content_kind === "document" ||
    canMoveItem(item) ||
    canStartTranscription(item);
  const canDeleteItem = (item: ManagedContentItem) => {
    if (item.content_kind === "media_transcript")
      return isSystemAdmin && Boolean(item.media_id);
    const requiresPublish =
      item.has_published_head ||
      !["draft", "rejected"].includes(item.lifecycle_status);
    return requiresPublish
      ? can("item.archive_published")
      : can("item.archive_draft");
  };
  const canPublishItem = (item: ManagedContentItem) =>
    item.content_kind === "document" &&
    ["approved", "publication_failed"].includes(item.lifecycle_status);

  const openBulkWorkbench = (action: "submit" | "approve" | "reject") => {
    const targets = selectedItems.filter(
      (item) =>
        item.content_kind === "document" &&
        (action === "submit"
          ? ["draft", "rejected"].includes(item.lifecycle_status)
          : item.lifecycle_status === "awaiting_review"),
    );
    setBulkWorkbenchTargets(targets);
    setBulkWorkbenchResults({});
    setBulkFailures([]);
    setBulkNote("");
    setBulkAction(action);
  };

  const executeBulkWorkbench = async (
    operation: "submit" | "approve" | "reject",
    versionIds: string[],
    single = false,
  ) => {
    if (versionIds.length === 0 || busyAction === "bulk" || bulkItemBusy)
      return;
    if (operation === "reject" && !bulkNote.trim()) {
      toast.error("退回资料前请填写退回原因");
      return;
    }
    if (single) setBulkItemBusy(versionIds[0]);
    else setBusyAction("bulk");
    setBulkFailures([]);
    try {
      const result =
        operation === "submit"
          ? await adminContentApi.bulkSubmit(versionIds)
          : await adminContentApi.bulkReview(
              versionIds,
              operation === "approve",
              bulkNote,
            );
      const succeededIds = new Set(
        result.results
          .filter((entry) => entry.status === "succeeded")
          .map((entry) => entry.version_id),
      );
      const titleById = new Map(
        bulkWorkbenchTargets.map((item) => [item.version_id, item.title]),
      );
      const failures = result.results
        .filter((entry) => entry.status === "failed")
        .map((entry) => ({
          ...entry,
          title: titleById.get(entry.version_id) || "未知资料",
        }));
      setBulkFailures(failures);
      setBulkWorkbenchResults((current) => {
        const next = { ...current };
        for (const versionId of succeededIds) {
          next[versionId] =
            operation === "submit"
              ? "submitted"
              : operation === "approve"
                ? "approved"
                : "rejected";
        }
        return next;
      });
      setSelected((current) =>
        current.filter((versionId) => !succeededIds.has(versionId)),
      );
      if (result.failed)
        toast.error(`成功 ${result.succeeded} 份，失败 ${result.failed} 份`);
      else
        toast.success(
          operation === "submit"
            ? `已提交 ${result.succeeded} 份资料审核`
            : operation === "approve"
              ? `已通过 ${result.succeeded} 份资料`
              : `已退回 ${result.succeeded} 份资料`,
        );
      await load(true);
    } catch (bulkError) {
      toast.error(
        bulkError instanceof Error ? bulkError.message : "批量操作失败",
      );
    } finally {
      if (single) setBulkItemBusy(null);
      else setBusyAction(null);
    }
  };

  const executeBulk = async () => {
    if (!bulkAction || selectedItems.length === 0 || busyAction === "bulk")
      return;
    if (bulkAction === "reject" && !bulkNote.trim()) return;
    setBusyAction("bulk");
    setBulkFailures([]);
    try {
      const actionItems =
        bulkAction === "publish"
          ? selectedItems.filter(canPublishItem)
          : selectedItems;
      const ids = actionItems.map((item) => item.version_id);
      const selectedMoveOperation = moveOperation(selectedItems[0]);
      const moveItems = selectedItems.map((item) => ({
        item_id: item.item_id,
        expected_version_id: item.version_id,
      }));
      const result =
        bulkAction === "move"
          ? selectedMoveOperation === "reclassify"
            ? await adminContentApi.bulkReclassify(moveItems, bulkMoveFolderId)
            : await adminContentApi.bulkMove(moveItems, bulkMoveFolderId)
          : bulkAction === "publish"
            ? await adminContentApi.bulkPublish(ids)
            : await adminContentApi.bulkReview(
                ids,
                bulkAction === "approve",
                bulkNote,
              );
      const titles = new Map(
        selectedItems.map((item) => [item.version_id, item.title]),
      );
      const failures = result.results
        .filter((entry) => entry.status === "failed")
        .map((entry) => ({
          ...entry,
          title: titles.get(entry.version_id) || "未知资料",
        }));
      setBulkFailures(failures);
      if (result.failed)
        toast.error(`成功 ${result.succeeded} 份，失败 ${result.failed} 份`);
      else
        toast.success(
          bulkAction === "publish"
            ? `已将 ${result.succeeded} 份资料加入发布队列`
            : bulkAction === "move" && selectedMoveOperation === "reclassify"
              ? `已提交 ${result.succeeded} 份资料的分类调整任务`
              : bulkAction === "move" && selectedMoveOperation === "archive"
                ? `${result.succeeded} 份视频转录稿的归档目录已调整`
                : bulkAction === "move"
                  ? `已移动 ${result.succeeded} 份资料`
                  : `已处理 ${result.succeeded} 份资料`,
        );
      setSelected(failures.map((entry) => entry.version_id));
      await load(true);
      if (!result.failed) setBulkAction(null);
    } catch (bulkError) {
      toast.error(
        bulkError instanceof Error ? bulkError.message : "批量操作失败",
      );
    } finally {
      setBusyAction(null);
    }
  };

  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const selectable = items.filter(canSelectItem);
  const selectableCount = sortedChildFolders.length + selectable.length;
  const allSelected =
    selectableCount > 0 &&
    sortedChildFolders
      .every((folder) => selectedFolders.includes(folder.id)) &&
    selectable
      .every((item) => selected.includes(item.version_id));
  const toggleAll = () => {
    if (allSelected) {
      setSelected([]);
      setSelectedFolders([]);
      return;
    }
    const folders = sortedChildFolders;
    setSelectedFolders(folders.map((folder) => folder.id));
    setSelected(
      selectable.map((item) => item.version_id),
    );
  };

  const createDestinationFolder = async (
    parentId: string,
    displayName: string,
  ) => {
    const siblings = categories.filter(
      (category) => category.parent_id === parentId && category.is_active,
    );
    const siblingNumber =
      Math.max(
        0,
        ...siblings.map((category) =>
          /^\d+$/.test(category.display_code)
            ? Number(category.display_code)
            : 0,
        ),
      ) + 1;
    const sortOrder =
      Math.max(0, ...siblings.map((category) => category.sort_order)) + 10;
    const created = await adminContentApi.createCategory({
      parent_id: parentId,
      display_code: String(siblingNumber).padStart(2, "0"),
      display_name: displayName,
      sort_order: sortOrder,
    });
    setCategories((current) => [
      ...current.filter((category) => category.id !== created.id),
      created,
    ]);
    toast.success("文件夹已创建并选中");
    return created;
  };
  const selectedFolderRows = categories.filter((category) =>
    selectedFolders.includes(category.id),
  );
  const totalSelectedCount = selected.length + selectedFolders.length;
  const toggleFolderSelection = (folderId: string) => {
    setSelectedFolders((current) =>
      current.includes(folderId)
        ? current.filter((id) => id !== folderId)
        : [...current, folderId],
    );
  };
  const openRecursiveBulk = (action: BulkOperationAction) =>
    setRecursiveBulkAction(action);
  const documentSelection = selectedItems.every(
    (item) => item.content_kind === "document",
  );
  const videoSelection =
    selectedItems.length > 0 &&
    selectedItems.every((item) => item.content_kind === "media_transcript");
  const hasTranscribableSelection =
    videoSelection && selectedItems.some(canStartTranscription);
  const hasSubmittableSelection =
    documentSelection &&
    selectedItems.some((item) =>
      ["draft", "rejected"].includes(item.lifecycle_status),
    );
  const hasReviewableSelection =
    documentSelection &&
    selectedItems.some((item) => item.lifecycle_status === "awaiting_review");
  const publishableSelectedItems = selectedItems.filter(canPublishItem);
  const skippedPublishSelectedItems = selectedItems.filter(
    (item) => !canPublishItem(item),
  );
  const hasPublishableSelection =
    documentSelection && publishableSelectedItems.length > 0;
  const selectedMoveOperations = new Set(selectedItems.map(moveOperation));
  const hasMovableSelection =
    selectedItems.length > 0 &&
    selectedItems.every(canMoveItem) &&
    selectedMoveOperations.size === 1;
  const bulkMoveLabel =
    selectedMoveOperations.size === 1 &&
    selectedMoveOperations.has("reclassify")
      ? "批量调整分类"
      : selectedMoveOperations.size === 1 &&
          selectedMoveOperations.has("archive")
        ? "批量调整归档目录"
        : "批量移动资料";
  const hasDeletableSelection =
    documentSelection && selectedItems.some(canDeleteItem);
  const hasDownloadableSelection =
    documentSelection && selectedItems.length > 1 && can("item.download");
  const bulkDisabled = Boolean(busyAction) || refreshing || !enabled;
  const bulkWorkbenchFailureById = new Map(
    bulkFailures.map((entry) => [entry.version_id, entry.message]),
  );
  const bulkPendingSubmitIds = bulkWorkbenchTargets
    .filter(
      (item) =>
        ["draft", "rejected"].includes(item.lifecycle_status) &&
        !bulkWorkbenchResults[item.version_id],
    )
    .map((item) => item.version_id);
  const bulkPendingReviewIds = bulkWorkbenchTargets
    .filter(
      (item) =>
        (item.lifecycle_status === "awaiting_review" ||
          bulkWorkbenchResults[item.version_id] === "submitted") &&
        !["approved", "rejected"].includes(
          bulkWorkbenchResults[item.version_id] || "",
        ),
    )
    .map((item) => item.version_id);
  const bulkSubmittedCount = Object.values(bulkWorkbenchResults).filter(
    (value) => value === "submitted",
  ).length;

  const openTranscriptionDialog = (
    targets: ManagedContentItem[],
    scope: "media" | "category" | "batch" = "media",
  ) => {
    const videos = targets.filter(
      (item) => item.content_kind === "media_transcript" && item.media_id,
    );
    setTranscriptionTargets(videos);
    setTranscriptionScope(scope);
    setTranscriptionPreflight(null);
    setTranscriptionRequestKey(createRequestId());
    setTranscriptionDialogOpen(true);
  };

  const runTranscriptionPreflight = async () => {
    if (!videoSchemeId || transcriptionDialogBusy || !transcriptionRequestKey)
      return;
    setTranscriptionDialogBusy(true);
    try {
      const body =
        transcriptionScope === "category"
          ? {
              scheme_id: videoSchemeId,
              request_idempotency_key: transcriptionRequestKey,
              category_id: currentFolderId,
              recursive: true,
            }
          : transcriptionScope === "batch"
            ? {
                scheme_id: videoSchemeId,
                request_idempotency_key: transcriptionRequestKey,
                upload_batch_id: activeUpload?.batchId || "",
              }
            : {
                scheme_id: videoSchemeId,
                request_idempotency_key: transcriptionRequestKey,
                media_ids: transcriptionTargets
                  .map((item) => item.media_id!)
                  .slice(0, 100),
              };
      setTranscriptionPreflight(
        await adminContentApi.preflightBulkTranscription(body),
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "转录预检失败");
    } finally {
      setTranscriptionDialogBusy(false);
    }
  };

  const executeTranscriptionStart = async () => {
    if (
      !videoSchemeId ||
      transcriptionDialogBusy ||
      !transcriptionPreflight ||
      !transcriptionRequestKey
    )
      return;
    setTranscriptionDialogBusy(true);
    try {
      const body =
        transcriptionScope === "category"
          ? {
              scheme_id: videoSchemeId,
              request_idempotency_key: transcriptionRequestKey,
              category_id: currentFolderId,
              recursive: true,
            }
          : transcriptionScope === "batch"
            ? {
                scheme_id: videoSchemeId,
                request_idempotency_key: transcriptionRequestKey,
                upload_batch_id: activeUpload?.batchId || "",
              }
            : {
                scheme_id: videoSchemeId,
                request_idempotency_key: transcriptionRequestKey,
                media_ids: transcriptionTargets
                  .map((item) => item.media_id!)
                  .slice(0, 100),
              };
      const result = await adminContentApi.bulkStartTranscription(body);
      if (result.failed)
        toast.error(`已启动 ${result.started} 个，${result.failed} 个未启动`);
      else toast.success(`已启动 ${result.started} 个视频转录任务`);
      setTranscriptionTargets([]);
      setTranscriptionPreflight(null);
      setTranscriptionRequestKey(null);
      setTranscriptionDialogOpen(false);
      await load(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "启动转录失败");
    } finally {
      setTranscriptionDialogBusy(false);
    }
  };

  const renderItemStatus = (item: ManagedContentItem) => {
    if (
      ACTIVE_RECLASSIFICATION_STATUSES.has(item.reclassification_status || "")
    ) {
      return <Badge variant="warning">分类调整中</Badge>;
    }
    if (item.reclassification_status === "failed") {
      return <Badge variant="destructive">分类调整失败</Badge>;
    }
    return (
      <Badge variant={statusVariant(item.lifecycle_status)}>
        {statusLabel[item.lifecycle_status] || "未知状态"}
      </Badge>
    );
  };

  const renderActions = (item: ManagedContentItem) => {
    const disabled = Boolean(busyAction) || refreshing || !enabled;
    const isMediaTranscript = item.content_kind === "media_transcript";
    const reclassifying = ACTIVE_RECLASSIFICATION_STATUSES.has(
      item.reclassification_status || "",
    );
    const previewable =
      item.doc_type === "xmind" ||
      Boolean(
        item.preview_parent_id &&
        ["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx"].includes(
          item.doc_type,
        ),
      );
    const movable = canMoveItem(item);
    const downloadable = can("item.download");
    const revisionAllowed =
      !isMediaTranscript &&
      can("item.upload") &&
      item.lifecycle_status !== "publishing" &&
      !reclassifying;
    const deletable =
      canDeleteItem(item) &&
      item.lifecycle_status !== "publishing" &&
      !reclassifying;
    const workflow =
      ["draft", "approved", "publication_failed"].includes(
        item.lifecycle_status,
      ) && can("item.publish")
        ? {
            label:
              item.lifecycle_status === "publication_failed"
                ? "重新发布"
                : "发布",
            action: () => openPublishDialog(item),
          }
        : null;
    const unavailableReason = busyAction
      ? "正在处理其他操作，请稍候"
      : refreshing
        ? "资料列表正在刷新，请稍候"
        : !enabled
          ? "资料管理功能当前不可用"
          : null;
    if (isMediaTranscript) {
      const pendingTranscription =
        canStartTranscription(item) &&
        !item.transcription_job_status?.match(/pending|running/);
      const transcriptionTooltip =
        unavailableReason ||
        (pendingTranscription
          ? "选择转录方案"
          : item.transcription_job_status?.match(/pending|running/)
            ? "转录任务进行中"
            : item.transcription_failure_classification === "permanent"
              ? "该视频无法重试转录"
              : "该视频已完成转录");
      const moveTooltip =
        unavailableReason ||
        (movable ? "调整归档目录" : "当前账号没有发布权限");
      const mediaManageAllowed =
        state.status === "authed" &&
        state.user.role === "admin" &&
        Boolean(item.media_id);
      const updateAllowed =
        mediaManageAllowed &&
        !item.transcription_job_status?.match(/pending|running/);
      const mediaBaseUrl = `/admin/content?view=transcription&media_id=${encodeURIComponent(item.media_id || "")}`;
      return (
        <div className="ml-auto flex min-h-10 max-w-full flex-wrap items-center justify-end gap-1">
          <IconButton label={`转录“${item.title}”`} tooltip={transcriptionTooltip} className="border border-border max-sm:size-10" disabled={disabled || !pendingTranscription} onClick={() => openTranscriptionDialog([item])}><Rocket className="size-4" /></IconButton>
          <IconButton label={`播放“${item.title}”`} tooltip={unavailableReason || (item.has_published_head ? "播放视频与转录稿" : "转录稿发布后可播放")} className="border border-border max-sm:size-10" disabled={disabled || !item.media_id || !item.has_published_head} onClick={() => openVideoPreview({ mediaId: item.media_id!, title: item.title, startSeconds: 0, fromSource: false })}><Film className="size-4" /></IconButton>
          <IconButton
            label={`查看“${item.title}”的详细信息`}
            tooltip={unavailableReason || "查看视频详细信息"}
            className="border border-border max-sm:size-10"
            disabled={disabled}
            onClick={() => setDetail(item)}
          >
            <Info className="size-4" />
          </IconButton>
          <IconButton label={`重命名“${item.title}”`} tooltip={unavailableReason || (mediaManageAllowed ? "重命名视频" : "仅系统管理员可以重命名视频")} className="border border-border max-sm:size-10" disabled={disabled || !mediaManageAllowed} onClick={() => openMediaInfoDialog(item)}><Pencil className="size-4" /></IconButton>
          <IconButton label={`更新“${item.title}”`} tooltip={unavailableReason || (updateAllowed ? "更新视频资料" : "转录任务进行中，暂不能更新视频")} className="border border-border max-sm:size-10" disabled={disabled || !updateAllowed} onClick={() => { window.location.href = `${mediaBaseUrl}&action=replace`; }}><FileUp className="size-4" /></IconButton>
          <IconButton
            label={`调整“${item.title}”的归档目录`}
            tooltip={moveTooltip}
            className="border border-border max-sm:size-10"
            disabled={disabled || !movable}
            onClick={() => {
              setMoveTarget(item);
              setMoveFolderId("");
              setMoveError(null);
            }}
          >
            <FolderInput className="size-4" />
          </IconButton>
          <IconButton label={`下载“${item.title}”`} tooltip={unavailableReason || (downloadable ? "选择下载视频、转录稿或两者" : "当前账号没有下载资料的权限")} className="border border-border max-sm:size-10" disabled={disabled || !downloadable || !item.media_id} onClick={() => openMediaDownload(item)}><Download className="size-4" /></IconButton>
          <ActionsMenu
            compact
            disabled={disabled}
            triggerLabel={`更多“${item.title}”的操作`}
            menuLabel={`“${item.title}”的更多操作`}
            options={[
              {
                key: "edit-transcript",
                label: "编辑转录稿",
                icon: <FilePenLine className="size-4" />,
                href: mediaManageAllowed
                  ? `${mediaBaseUrl}&workbench=1&action=edit-current`
                  : undefined,
                disabled: !mediaManageAllowed,
                disabledReason: "当前账号没有发布权限",
              },
              {
                key: "edit-media-info",
                label: "编辑媒体信息",
                icon: <Pencil className="size-4" />,
                disabled: !mediaManageAllowed,
                disabledReason: "当前账号没有发布权限",
                onSelect: () => openMediaInfoDialog(item),
              },
              {
                key: "replace-video",
                label: "替换视频",
                icon: <Video className="size-4" />,
                href: mediaManageAllowed
                  ? `${mediaBaseUrl}&action=replace`
                  : undefined,
                disabled: !mediaManageAllowed,
                disabledReason: "当前账号没有发布权限",
              },
              {
                key: "open-media",
                label: "进入转录任务",
                icon: <ExternalLink className="size-4" />,
                href: mediaManageAllowed
                  ? `${mediaBaseUrl}&workbench=1`
                  : undefined,
                disabled: !mediaManageAllowed,
                disabledReason: item.media_id
                  ? "仅系统管理员可以进入转录任务"
                  : "媒体关联缺失",
              },
            ]}
          />
          <IconButton label={`删除“${item.title}”`} tooltip={unavailableReason || (deletable ? "移入回收站（视频与转录稿可一起恢复）" : "仅系统管理员可以删除视频")} className="border border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive max-sm:size-10" disabled={disabled || !deletable} onClick={() => openDeleteDialog([item])}><Trash2 className="size-4" /></IconButton>
        </div>
      );
    }
    const previewTooltip =
      unavailableReason ||
      (previewable
        ? "预览文件"
        : item.preview_status === "missing" && item.doc_type === "pptx"
          ? "PPTX 预览生成失败，可在资料详情中重新生成"
          : item.preview_status === "pending"
            ? "发布完成后可在线预览"
            : !item.preview_parent_id
              ? "该资料尚未生成可预览文件"
              : "当前文件格式暂不支持在线预览");
    const moveTooltip =
      unavailableReason ||
      (movable
        ? item.has_published_head
          ? "调整分类"
          : "移动资料"
        : ACTIVE_RECLASSIFICATION_STATUSES.has(
              item.reclassification_status || "",
            )
          ? "分类调整正在同步索引和目录"
          : item.has_published_head
            ? item.is_current && item.lifecycle_status === "published"
              ? "当前账号没有调整已发布资料分类的权限"
              : "存在待处理的新版本，暂时不能调整正式分类"
            : !["draft", "rejected", "awaiting_review"].includes(
                  item.lifecycle_status,
                )
              ? "仅草稿、已退回或待确认的资料可以移动"
              : item.lifecycle_status === "awaiting_review"
                ? "当前账号没有移动待确认资料的权限"
                : "当前账号没有移动草稿或已退回资料的权限");
    const revisionTooltip =
      unavailableReason ||
      (revisionAllowed
        ? "重命名资料"
        : item.lifecycle_status === "publishing"
          ? "资料正在发布，暂不能重命名"
          : reclassifying
            ? "资料正在调整分类，暂不能重命名"
            : "当前账号没有上传和修改资料的权限");
    const updateTooltip =
      unavailableReason ||
      (revisionAllowed
        ? "更新资料文件"
        : item.lifecycle_status === "publishing"
          ? "资料正在发布，暂不能更新文件"
          : reclassifying
            ? "资料正在调整分类，暂不能更新文件"
            : "当前账号没有上传和修改资料的权限");
    const deleteTooltip =
      unavailableReason ||
      (deletable
        ? "移入回收站"
        : item.lifecycle_status === "publishing"
          ? "资料正在发布，暂不能移入回收站"
          : reclassifying
            ? "资料正在调整分类，暂不能移入回收站"
            : item.has_published_head ||
                !["draft", "rejected"].includes(item.lifecycle_status)
              ? "当前账号没有删除已审核或已发布资料的权限"
              : "当前账号没有删除草稿或已退回资料的权限");
    return (
      <div className="ml-auto flex w-full flex-col items-stretch gap-2 lg:w-auto lg:flex-row lg:items-center lg:justify-end">
        {workflow && (
          <Button
            size="sm"
            className="w-full shrink-0 max-sm:h-10 lg:w-auto"
            disabled={disabled}
            onClick={workflow.action}
          >
            {workflow.label}
          </Button>
        )}
        <div className="ml-auto flex min-h-10 max-w-full flex-wrap items-center justify-end gap-1 lg:ml-0 lg:w-auto lg:flex-nowrap">
          <IconButton
            label={`预览“${item.title}”`}
            tooltip={previewTooltip}
            className="border border-border max-sm:size-10"
            disabled={disabled || !previewable}
            onClick={() =>
              item.doc_type === "xmind"
                ? openXMind(item.version_id, item.title)
                : openDocumentPreview(
                    item.preview_parent_id!,
                    item.title,
                    item.doc_type,
                    1,
                    {},
                    null,
                  )
            }
          >
            <Eye className="size-4" />
          </IconButton>
          <IconButton
            label={`查看“${item.title}”的详细信息`}
            tooltip={unavailableReason || "查看资料详情"}
            className="border border-border max-sm:size-10"
            disabled={disabled}
            onClick={() => setDetail(item)}
          >
            <Info className="size-4" />
          </IconButton>
          <IconButton
            label={`重命名“${item.title}”`}
            tooltip={revisionTooltip}
            className="border border-border max-sm:size-10"
            disabled={disabled || !revisionAllowed}
            onClick={() => openRenameDialog(item)}
          >
            <Pencil className="size-4" />
          </IconButton>
          <IconButton
            label={`更新“${item.title}”`}
            tooltip={updateTooltip}
            className="border border-border max-sm:size-10"
            disabled={disabled || !revisionAllowed}
            onClick={() => openUpdateDialog(item)}
          >
            <FileUp className="size-4" />
          </IconButton>
          <IconButton
            label={
              item.has_published_head
                ? `调整“${item.title}”的分类`
                : `移动“${item.title}”`
            }
            tooltip={moveTooltip}
            className="border border-border max-sm:size-10"
            disabled={disabled || !movable}
            onClick={() => {
              setMoveTarget(item);
              setMoveFolderId("");
              setMoveError(null);
            }}
          >
            <FolderInput className="size-4" />
          </IconButton>
          <IconButton
            label={`下载“${item.title}”`}
            tooltip={
              unavailableReason ||
              (downloadable ? "下载文件" : "当前账号没有下载文件的权限")
            }
            className="border border-border max-sm:size-10"
            disabled={disabled || !downloadable}
            onClick={() => void downloadContent(item)}
          >
            <Download className="size-4" />
          </IconButton>
          <IconButton
            label={`删除“${item.title}”`}
            tooltip={deleteTooltip}
            className="border border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive max-sm:size-10"
            disabled={disabled || !deletable}
            onClick={() => openDeleteDialog([item])}
          >
            <Trash2 className="size-4" />
          </IconButton>
        </div>
      </div>
    );
  };

  const renderFolderActions = (folder: ManagedCategory) => {
    const folderLabel = `${folder.display_code} ${folder.display_name}`;
    const disabled = Boolean(busyAction) || refreshing || !enabled;
    const folderDownloading = busyAction === `folder:${folder.id}:download`;
    const unavailableReason = busyAction
      ? "正在处理其他操作，请稍候"
      : refreshing
        ? "资料列表正在刷新，请稍候"
        : !enabled
          ? "资料管理功能当前不可用"
          : null;
    const downloadTooltip =
      unavailableReason ||
      (!can("item.download")
        ? "当前账号没有下载资料的权限"
        : (folder.total_item_count ?? folder.item_count) < 1
          ? "文件夹内没有可下载的资料"
          : "打包下载整个文件夹");
    return (
      <div
        className="ml-auto flex min-h-10 shrink-0 items-center justify-end gap-1"
        onClick={(event) => event.stopPropagation()}
      >
        <IconButton
          label={`打开文件夹“${folderLabel}”`}
          tooltip={unavailableReason || "打开文件夹"}
          className="border border-border max-sm:size-10"
          disabled={disabled}
          onClick={() => navigateToFolder(folder.id)}
        >
          <ChevronRight className="size-4" />
        </IconButton>
        <IconButton
          label={`查看文件夹“${folderLabel}”的详细信息`}
          tooltip={unavailableReason || "查看文件夹详情"}
          className="border border-border max-sm:size-10"
          disabled={disabled}
          onClick={() => setFolderDetailTarget(folder)}
        >
          <Info className="size-4" />
        </IconButton>
        {can("category.manage") && (
          <>
            <IconButton
              label={`重命名文件夹“${folderLabel}”`}
              tooltip={unavailableReason || "重命名文件夹"}
              className="border border-border max-sm:size-10"
              disabled={disabled}
              onClick={() => openFolderRename(folder)}
            >
              <Pencil className="size-4" />
            </IconButton>
            <IconButton
              label={`调整文件夹“${folderLabel}”的编号`}
              tooltip={unavailableReason || "调整文件夹编号"}
              className="border border-border max-sm:size-10"
              disabled={disabled}
              onClick={() => openFolderNumber(folder)}
            >
              <ListOrdered className="size-4" />
            </IconButton>
            <IconButton
              label={`移动文件夹“${folderLabel}”`}
              tooltip={unavailableReason || "移动文件夹位置"}
              className="border border-border max-sm:size-10"
              disabled={disabled}
              onClick={() => openFolderMove(folder)}
            >
              <FolderInput className="size-4" />
            </IconButton>
          </>
        )}
        <IconButton
          label={`打包下载文件夹“${folderLabel}”`}
          tooltip={downloadTooltip}
          className="border border-border max-sm:size-10"
          disabled={
            disabled ||
            !can("item.download") ||
            (folder.total_item_count ?? folder.item_count) < 1 ||
            folderDownloading
          }
          onClick={() => void downloadFolder(folder)}
        >
          <Download
            className={folderDownloading ? "size-4 animate-pulse" : "size-4"}
          />
        </IconButton>
        {can("category.manage") && (
          <IconButton
            label={`删除文件夹“${folderLabel}”`}
            tooltip={unavailableReason || "删除文件夹"}
            className="border border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive max-sm:size-10"
            disabled={disabled}
            onClick={() => setFolderDeleteTarget(folder)}
          >
            <Trash2 className="size-4" />
          </IconButton>
        )}
      </div>
    );
  };

  const selectView = (nextView: ManagedContentView) => {
    setView(nextView);
    if (nextView === "library" || nextView === "trash") setPage(0);
    if (nextView !== "library") setSelected([]);
  };
  const viewTabs = (can("trash.view") ||
    can("item.upload") ||
    can("index.view") ||
    (state.status === "authed" && state.user.role === "admin")) && (
    <div className="flex flex-wrap gap-2" role="tablist" aria-label="资料视图">
      <Button
        size="sm"
        variant={view === "library" ? "default" : "outline"}
        role="tab"
        aria-selected={view === "library"}
        onClick={() => selectView("library")}
      >
        <Folder className="size-4" />
        资料列表
      </Button>
      {can("trash.view") && (
        <Button
          size="sm"
          variant={view === "trash" ? "default" : "outline"}
          role="tab"
          aria-selected={view === "trash"}
          onClick={() => selectView("trash")}
        >
          <Trash2 className="size-4" />
          回收站
        </Button>
      )}
      {can("item.upload") && (
        <Button
          size="sm"
          variant={view === "uploads" ? "default" : "outline"}
          role="tab"
          aria-selected={view === "uploads"}
          onClick={() => selectView("uploads")}
        >
          <Upload className="size-4" />
          上传任务
        </Button>
      )}
      {can("index.view") && (
        <Button
          size="sm"
          variant={view === "index" ? "default" : "outline"}
          role="tab"
          aria-selected={view === "index"}
          onClick={() => selectView("index")}
        >
          <ListChecks className="size-4" />
          索引任务
        </Button>
      )}
      {state.status === "authed" && state.user.role === "admin" && (
        <Button
          size="sm"
          variant={view === "transcription" ? "default" : "outline"}
          role="tab"
          aria-selected={view === "transcription"}
          onClick={() => selectView("transcription")}
        >
          <Video className="size-4" />
          转录任务
        </Button>
      )}
    </div>
  );

  const auditDialog = (
    <Dialog
      open={Boolean(auditTarget)}
      onOpenChange={(open) => {
        if (!open) {
          setAuditTarget(null);
          setAuditEvents([]);
          setAuditError(null);
        }
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>操作记录</DialogTitle>
          <DialogDescription>
            “{auditTarget?.title}”移入回收站和恢复的历史记录。
          </DialogDescription>
        </DialogHeader>
        {auditLoading ? (
          <LoadingState
            className="min-h-36 border-0"
            label="正在加载操作记录…"
          />
        ) : auditError ? (
          <ErrorState
            title="操作记录加载失败"
            description={auditError}
            action={
              auditTarget ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void openAudit(auditTarget)}
                >
                  重新加载
                </Button>
              ) : undefined
            }
          />
        ) : auditEvents.length === 0 ? (
          <EmptyState
            className="min-h-36 rounded-none border-0"
            title="暂无操作记录"
            description="移入回收站或恢复后会在这里留下记录。"
          />
        ) : (
          <ol className="max-h-[55vh] divide-y divide-border overflow-y-auto rounded-ui-md border border-border">
            {auditEvents.map((event, index) => (
              <li
                key={`${event.created_at}-${event.event_type}-${index}`}
                className="space-y-2 px-4 py-3 text-ui-sm"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="font-medium">
                    {event.event_type === "content.restored"
                      ? "恢复资料"
                      : event.archive_reason === "restore_conflict_replacement"
                        ? "同名替换移入回收站"
                        : "移入回收站"}
                  </p>
                  <time className="text-ui-xs text-muted-foreground">
                    {formatAdminDate(event.created_at)}
                  </time>
                </div>
                <p className="text-muted-foreground">
                  操作人：{event.actor_name || "未知人员"}
                </p>
                {event.event_type === "content.restored" && (
                  <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1">
                    <dt className="text-muted-foreground">恢复结果</dt>
                    <dd>
                      {statusLabel[event.restored_status || ""] ||
                        event.restored_status ||
                        "已恢复"}
                    </dd>
                    <dt className="text-muted-foreground">目标目录</dt>
                    <dd className="break-words">
                      {event.target_category_path || "原目录"}
                    </dd>
                    <dt className="text-muted-foreground">处理方式</dt>
                    <dd>
                      {event.restore_strategy === "replace_conflict"
                        ? "替换同名资料"
                        : event.restore_strategy === "alternate_directory"
                          ? "恢复到其他目录"
                          : "恢复到原目录"}
                    </dd>
                    {event.replaced_title && (
                      <>
                        <dt className="text-muted-foreground">被替换资料</dt>
                        <dd className="break-words">
                          {event.replaced_title}
                          {event.replaced_filename
                            ? `（${event.replaced_filename}）`
                            : ""}
                        </dd>
                      </>
                    )}
                  </dl>
                )}
              </li>
            ))}
          </ol>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => setAuditTarget(null)}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );

  if (view === "index") {
    return (
      <section className="space-y-5" aria-labelledby="managed-content-title">
        <header>
          <p className="text-ui-xs font-medium text-primary">内容管理</p>
          <h1
            id="managed-content-title"
            className="mt-1 text-ui-2xl font-semibold tracking-tight"
          >
            资料管理
          </h1>
          <p className="mt-1 text-ui-sm text-muted-foreground">
            统一管理资料的上传、分类、确认和发布。
          </p>
        </header>
        {viewTabs}
        <AdminDocumentsPage embedded />
      </section>
    );
  }

  if (view === "uploads") {
    return (
      <section className="space-y-5" aria-labelledby="managed-content-title">
        <header>
          <p className="text-ui-xs font-medium text-primary">内容管理</p>
          <h1
            id="managed-content-title"
            className="mt-1 text-ui-2xl font-semibold tracking-tight"
          >
            资料管理
          </h1>
          <p className="mt-1 text-ui-sm text-muted-foreground">
            统一管理资料的上传、分类、确认和发布。
          </p>
        </header>
        {viewTabs}
        <UploadTasksPanel
          activeUpload={activeUpload}
          canTranscribe={isSystemAdmin}
          canRetry={(task) =>
            Boolean(lastUploadAttempt?.batchId === task.batch_id)
          }
          onRetry={(task) => void retryUploadTask(task)}
        />
        {activeUpload && <div className="fixed right-4 top-4 z-50 w-[min(24rem,calc(100vw-2rem))] rounded-ui-lg border border-primary/40 bg-background px-4 py-3 shadow-surface" role="status" aria-live="polite"><div className="flex items-start justify-between gap-3"><div><p className="font-medium">{activeUpload.phase === "processing" ? "服务端处理中…" : activeUpload.phase === "completed" ? "上传完成" : activeUpload.phase === "failed" ? "上传失败" : "上传中"}</p><p className="mt-1 text-ui-xs text-muted-foreground">{activeUpload.totalFiles} 个文件 · {activeUpload.targetPath}</p>{activeUpload.message && <p className="mt-1 text-ui-xs">{activeUpload.message}</p>}</div><button type="button" className="text-ui-xs text-muted-foreground" aria-label="关闭上传提示" onClick={() => setActiveUpload(null)}>关闭</button></div></div>}
      </section>
    );
  }

  if (view === "transcription") {
    return (
      <section className="space-y-5" aria-labelledby="managed-content-title">
        <header>
          <p className="text-ui-xs font-medium text-primary">内容管理</p>
          <h1
            id="managed-content-title"
            className="mt-1 text-ui-2xl font-semibold tracking-tight"
          >
            资料管理
          </h1>
          <p className="mt-1 text-ui-sm text-muted-foreground">
            统一管理资料的上传、分类、确认和发布。
          </p>
        </header>
        {viewTabs}
        <AdminTranscriptionTasksPage />
      </section>
    );
  }

  if (view === "trash") {
    const trashPageCount = Math.max(1, Math.ceil(trashTotal / PAGE_SIZE));
    const trashAllSelected =
      trashItems.length > 0 &&
      trashItems.every((item) => trashSelected.includes(item.version_id));
    const toggleAllTrash = () =>
      setTrashSelected(
        trashAllSelected
          ? []
          : trashItems.map((item) => item.version_id),
      );
    const retentionText = (item: ManagedContentItem) =>
      item.retention_status === "overdue"
        ? `已超期 ${Math.abs(item.retention_days_remaining || 0)} 天`
        : item.retention_status === "expiring"
          ? `即将到期，剩余 ${item.retention_days_remaining || 0} 天`
        : `保留中，剩余 ${item.retention_days_remaining || 0} 天`;
    const sortedTrashItems = [...trashItems].sort((left, right) => {
      const previousStatus = (item: ManagedContentItem) => item.pre_archive_lifecycle_status || item.lifecycle_status || "";
      const value = (item: ManagedContentItem): string | number => {
        switch (trashSort.key) {
          case "title": return item.title || "";
          case "category": return item.category_path || item.category_label || "";
          case "status": return statusLabel[previousStatus(item)] || "未知状态";
          case "source": return sourceLabel[item.source_origin] || "其他来源";
          case "retention": return item.retention_days_remaining || 0;
          case "archivedAt": return item.archived_at || 0;
        }
      };
      const a = value(left); const b = value(right);
      const comparison = typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b), "zh-CN", { numeric: true, sensitivity: "base" });
      return trashSort.direction === "asc" ? comparison : -comparison;
    });
    const toggleTrashSort = (key: TrashSortKey) => {
      setTrashSort((current) => current.key === key ? { key, direction: current.direction === "asc" ? "desc" : "asc" } : { key, direction: "asc" });
      setPage(0);
    };
    const trashSortIcon = (key: TrashSortKey) => trashSort.key !== key ? <ArrowUpDown className="size-3.5" /> : trashSort.direction === "asc" ? <ArrowUp className="size-3.5" /> : <ArrowDown className="size-3.5" />;
    return (
      <section className="space-y-5" aria-labelledby="managed-content-title">
        <header>
          <p className="text-ui-xs font-medium text-primary">内容管理</p>
          <h1
            id="managed-content-title"
            className="mt-1 text-ui-2xl font-semibold tracking-tight"
          >
            回收站
          </h1>
          <p className="mt-1 text-ui-sm text-muted-foreground">
            {can("trash.restore")
              ? "查看和恢复已移出资料库的资料。"
              : "查看已移出资料库的资料。"}
          </p>
        </header>
        {viewTabs}
        <section
          className="grid grid-cols-2 gap-3 lg:grid-cols-4"
          aria-label="回收站资料状态概览"
        >
          <ManagedSummaryCard
            label="全部资料"
            value={trashTotal}
            icon={<Archive className="size-4" />}
            onClick={() => {
              setTrashRetentionFilter("");
              setPage(0);
            }}
            active={!trashRetentionFilter}
          />
          <ManagedSummaryCard
            label="保留中"
            value={trashRetentionCounts.retained || 0}
            icon={<Archive className="size-4" />}
            tone="success"
            onClick={() => {
              setTrashRetentionFilter("retained");
              setPage(0);
            }}
            active={trashRetentionFilter === "retained"}
          />
          <ManagedSummaryCard
            label="即将到期"
            value={trashRetentionCounts.expiring || 0}
            icon={<AlertTriangle className="size-4" />}
            tone="warning"
            onClick={() => {
              setTrashRetentionFilter("expiring");
              setPage(0);
            }}
            active={trashRetentionFilter === "expiring"}
          />
          <ManagedSummaryCard
            label="已超期"
            value={trashRetentionCounts.overdue || 0}
            icon={<Trash2 className="size-4" />}
            tone="destructive"
            onClick={() => {
              setTrashRetentionFilter("overdue");
              setPage(0);
            }}
            active={trashRetentionFilter === "overdue"}
          />
        </section>
        {error && (
          <ErrorState
            title="回收站加载失败"
            description={error}
            action={
              <Button
                size="sm"
                variant="outline"
                onClick={() => void loadTrash()}
              >
                重新加载
              </Button>
            }
          />
        )}
        <Card
          className="shadow-surface [&_table]:!min-w-[68rem]"
          aria-labelledby="trash-list-title"
        >
          <div className="grid gap-3 border-b border-border px-4 py-4 xl:grid-cols-[minmax(13rem,1fr)_18rem_auto] xl:items-end min-[1400px]:grid-cols-[minmax(13rem,1fr)_24rem_auto] sm:px-5">
            <div className="min-w-0">
              <h2 id="trash-list-title" className="text-ui-base font-semibold">
                回收站资料
              </h2>
              <p className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-ui-xs text-muted-foreground">
                <span>共 {trashTotal} 份</span>
                <span role="status" aria-live="polite">
                  ·{" "}
                  {trashSelected.length > 0 ? (
                    <>
                      已选择 <strong>{trashSelected.length}</strong>{" "}
                      份
                    </>
                  ) : (
                    <>未选择资料</>
                  )}
                </span>
              </p>
            </div>
            <TrashSearchFilters
              queryInput={queryInput}
              retentionFilter={trashRetentionFilter}
              retentionCounts={trashRetentionCounts}
              categoryFilter={trashCategoryFilter}
              archivedBy={trashArchivedBy}
              archivedFrom={trashArchivedFrom}
              archivedTo={trashArchivedTo}
              categories={categories}
              onQueryInputChange={setQueryInput}
              onRetentionFilterChange={(value) => {
                setTrashRetentionFilter(value);
                setPage(0);
              }}
              onCategoryFilterChange={(value) => {
                setTrashCategoryFilter(value);
                setPage(0);
              }}
              onArchivedByChange={(value) => {
                setTrashArchivedBy(value);
                setPage(0);
              }}
              onArchivedFromChange={(value) => {
                setTrashArchivedFrom(value);
                setPage(0);
              }}
              onArchivedToChange={(value) => {
                setTrashArchivedTo(value);
                setPage(0);
              }}
              onClear={() => {
                setQueryInput("");
                setTrashRetentionFilter("");
                setTrashCategoryFilter("");
                setTrashArchivedBy("");
                setTrashArchivedFrom("");
                setTrashArchivedTo("");
                setPage(0);
              }}
            />
            <div className="flex shrink-0 flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                className="max-sm:h-control-md"
                onClick={() => void loadTrash()}
                disabled={trashLoading}
              >
                <RefreshCw
                  className={trashLoading ? "size-4 animate-spin" : "size-4"}
                />
                {trashLoading ? "刷新中…" : "刷新列表"}
              </Button>
              {(can("trash.policy_manage") || can("trash.purge")) && (
                <ActionsMenu
                  disabled={Boolean(busyAction)}
                  triggerLabel="回收站管理"
                  menuLabel="回收站管理"
                  options={[
                    ...(can("trash.policy_manage")
                      ? [
                          {
                            key: "trash-policy",
                            label: "清理策略",
                            icon: <SlidersHorizontal className="size-4" />,
                            onSelect: () => void openTrashSettings(),
                          },
                        ]
                      : []),
                    ...(can("trash.purge")
                      ? [
                          {
                            key: "trash-overdue",
                            label: "清理已超期资料",
                            icon: <Trash2 className="size-4" />,
                            destructive: true,
                            onSelect: () => void previewOverdueTrashPurge(),
                          },
                        ]
                      : []),
                    {
                      key: "trash-export",
                      label: busyAction === "trash-export" ? "导出中…" : "导出处置清单",
                      icon: <Download className="size-4" />,
                      onSelect: () => void exportTrash(),
                    },
                  ]}
                />
              )}
              {(can("trash.restore") || can("trash.purge")) && (
                <ActionsMenu
                  disabled={Boolean(busyAction) || trashSelected.length === 0}
                  triggerLabel="批量操作"
                  menuLabel="回收站批量操作"
                  options={[
                    ...(can("trash.restore") ? [{ key: "trash-restore-selected", label: `恢复所选（${trashSelected.length}）`, icon: <ArchiveRestore className="size-4" />, onSelect: () => { setTrashBulkTarget("original"); setTrashPreflight([]); setTrashPreflightOpen(true); } }] : []),
                    ...(can("trash.purge") ? [{ key: "trash-purge-selected", label: `永久删除所选（${trashSelected.length}）`, icon: <Trash2 className="size-4" />, destructive: true, onSelect: () => void openTrashPurge() }] : []),
                  ]}
                />
              )}
            </div>
            {trashSelected.length > 0 && (
              <div className="flex flex-col gap-2 rounded-ui-md border border-primary/30 bg-primary/5 px-3 py-2 sm:flex-row sm:items-center xl:col-span-3">
                <p className="mr-auto text-ui-sm">
                  已选择 <strong>{trashSelected.length}</strong> 份资料
                </p>
                <Button
                  size="sm"
                  className="max-sm:h-control-md"
                  disabled={Boolean(busyAction) || !can("trash.restore")}
                  title={!can("trash.restore") ? "当前账号没有恢复资料权限" : undefined}
                  onClick={() => {
                    setTrashBulkTarget("original");
                    setTrashPreflight([]);
                    setTrashPreflightOpen(true);
                  }}
                >
                  <ArchiveRestore className="size-4" />
                  恢复所选（{trashSelected.length}）
                </Button>
                {can("trash.purge") && (
                  <ActionsMenu
                    disabled={Boolean(busyAction)}
                    triggerLabel="批量操作"
                    menuLabel="回收站批量操作"
                    options={[
                      {
                        key: "trash-purge-selected",
                        label: `永久删除所选（${trashSelected.length}）`,
                        icon: <Trash2 className="size-4" />,
                        destructive: true,
                        onSelect: () => void openTrashPurge(),
                      },
                    ]}
                  />
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  className="max-sm:h-control-md"
                  disabled={Boolean(busyAction)}
                  onClick={() => setTrashSelected([])}
                >
                  取消选择
                </Button>
              </div>
            )}
          </div>
          {trashLoading ? (
            <LoadingState
              className="min-h-48 border-0"
              label="正在加载回收站…"
            />
          ) : trashItems.length === 0 ? (
            <EmptyState
              className="rounded-none border-0"
              title="回收站为空"
              description="移至回收站的资料会显示在这里。"
            />
          ) : (
            <>
              <div className="hidden overflow-x-auto lg:block">
                <table className="w-full text-ui-sm">
                  <thead className="border-b border-border bg-surface-muted text-left text-muted-foreground">
                    <tr>
                      {(can("trash.restore") || can("trash.purge")) && (
                        <th className="w-12 px-3 py-3">
                          <Checkbox
                            aria-label="选择当前页回收站资料"
                            checked={trashAllSelected}
                            onChange={toggleAllTrash}
                          />
                        </th>
                      )}
                      {([ ["title", "资料"], ["category", "原目录"], ["status", "原状态"], ["source", "来源"], ["retention", "保留期限"], ["archivedAt", "移入回收站"] ] as [TrashSortKey, string][]).map(([key, label]) => (
                        <th key={key} aria-sort={trashSort.key === key ? trashSort.direction === "asc" ? "ascending" : "descending" : "none"} className="px-3 py-3 font-medium">
                          <button type="button" className="inline-flex items-center gap-1 rounded px-1 py-0.5 hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => toggleTrashSort(key)}>
                            {label}{trashSortIcon(key)}
                          </button>
                        </th>
                      ))}
                      <th className="px-3 py-3 text-right font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {sortedTrashItems.map((item) => {
                      const previousStatus =
                        item.pre_archive_lifecycle_status ||
                        item.lifecycle_status;
                      const sourcePath =
                        item.source_rel_path &&
                        item.source_rel_path !== item.original_filename
                          ? item.source_rel_path
                          : null;
                      const checked = trashSelected.includes(item.version_id);
                      return (
                        <tr
                          key={item.item_id}
                          className={`align-middle transition-colors duration-normal hover:bg-surface-muted/60 ${checked ? "bg-primary/5" : ""}`}
                        >
                          {(can("trash.restore") || can("trash.purge")) && (
                            <td className="px-3 py-3">
                              <Checkbox
                                aria-label={`选择恢复“${item.title}”`}
                                checked={checked}
                                onChange={() =>
                                  setTrashSelected((current) =>
                                    current.includes(item.version_id)
                                      ? current.filter(
                                          (id) => id !== item.version_id,
                                        )
                                      : [...current, item.version_id],
                                  )
                                }
                              />
                            </td>
                          )}
                          <td className="max-w-xs px-3 py-3">
                            <p className="break-words font-medium">
                              {item.title}
                            </p>
                            <p className="mt-1 break-all text-ui-xs text-muted-foreground">
                              {item.original_filename} · v{item.version_number}
                            </p>
                          </td>
                          <td className="max-w-sm px-3 py-3">
                            <p className="break-words">
                              {item.category_path || item.category_label}
                            </p>
                            {sourcePath && (
                              <p className="mt-1 break-all text-ui-xs text-muted-foreground">
                                上传路径：{sourcePath}
                              </p>
                            )}
                          </td>
                          <td className="px-3 py-3">
                            <Badge variant={statusVariant(previousStatus)}>
                              {statusLabel[previousStatus] || "未知状态"}
                            </Badge>
                          </td>
                          <td className="px-3 py-3">
                            {sourceLabel[item.source_origin] || "其他来源"}
                          </td>
                          <td className="whitespace-nowrap px-3 py-3 text-ui-xs text-muted-foreground">
                            {retentionText(item)}
                          </td>
                          <td className="px-3 py-3">
                            <p>{item.archived_by_name || "未知人员"}</p>
                            <p className="mt-1 whitespace-nowrap text-ui-xs text-muted-foreground">
                              {formatAdminDate(item.archived_at)}
                            </p>
                          </td>
                          <td className="px-3 py-3">
                            <div className="flex justify-end gap-1">
                              <IconButton
                                label={`查看“${item.title}”的回收站记录`}
                                tooltip="查看操作记录"
                                className="size-control-sm border border-border"
                                onClick={() => void openAudit(item)}
                              >
                                <History className="size-4" />
                              </IconButton>
                              {can("trash.restore") && (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  disabled={Boolean(busyAction)}
                                  onClick={() => openRestore(item)}
                                >
                                  <ArchiveRestore className="size-4" />
                                  恢复
                                </Button>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <ul className="divide-y divide-border lg:hidden">
                {sortedTrashItems.map((item) => {
                  const previousStatus =
                    item.pre_archive_lifecycle_status || item.lifecycle_status;
                  const sourcePath =
                    item.source_rel_path &&
                    item.source_rel_path !== item.original_filename
                      ? item.source_rel_path
                      : null;
                  const checked = trashSelected.includes(item.version_id);
                  return (
                    <li
                      key={item.item_id}
                      className={`space-y-3 px-4 py-4 sm:px-5 ${checked ? "bg-primary/5" : ""}`}
                    >
                      <div className="flex items-start gap-3">
                        {(can("trash.restore") || can("trash.purge")) && (
                          <Checkbox
                            className="mt-0.5"
                            aria-label={`选择恢复“${item.title}”`}
                            checked={checked}
                            onChange={() =>
                              setTrashSelected((current) =>
                                current.includes(item.version_id)
                                  ? current.filter(
                                      (id) => id !== item.version_id,
                                    )
                                  : [...current, item.version_id],
                              )
                            }
                          />
                        )}
                        <div className="min-w-0 flex-1">
                          <p className="break-words font-medium">
                            {item.title}
                          </p>
                          <p className="mt-1 break-all text-ui-xs text-muted-foreground">
                            {item.original_filename} · v{item.version_number}
                          </p>
                        </div>
                        <Badge
                          className="shrink-0"
                          variant={statusVariant(previousStatus)}
                        >
                          {statusLabel[previousStatus] || "未知状态"}
                        </Badge>
                      </div>
                      <dl className="grid grid-cols-[5rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-ui-sm">
                        <dt className="text-muted-foreground">原目录</dt>
                        <dd className="break-words">
                          {item.category_path || item.category_label}
                        </dd>
                        {sourcePath && (
                          <>
                            <dt className="text-muted-foreground">上传路径</dt>
                            <dd className="break-all">{sourcePath}</dd>
                          </>
                        )}
                        <dt className="text-muted-foreground">来源</dt>
                        <dd>{sourceLabel[item.source_origin] || "其他来源"}</dd>
                        <dt className="text-muted-foreground">保留期限</dt>
                        <dd>{retentionText(item)}</dd>
                        <dt className="text-muted-foreground">移入人员</dt>
                        <dd>{item.archived_by_name || "未知人员"}</dd>
                        <dt className="text-muted-foreground">移入时间</dt>
                        <dd>{formatAdminDate(item.archived_at)}</dd>
                      </dl>
                      <div className="flex items-center justify-end gap-2">
                        <IconButton
                          label={`查看“${item.title}”的回收站记录`}
                          tooltip="查看操作记录"
                          className="size-control-sm border border-border max-sm:size-control-md"
                          onClick={() => void openAudit(item)}
                        >
                          <History className="size-4" />
                        </IconButton>
                        {can("trash.restore") && (
                          <Button
                            size="sm"
                            variant="outline"
                            className="max-sm:h-control-md"
                            disabled={Boolean(busyAction)}
                            onClick={() => openRestore(item)}
                          >
                            <ArchiveRestore className="size-4" />
                            恢复
                          </Button>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
          <div className="flex flex-col gap-2 border-t border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5">
            <p className="text-ui-xs text-muted-foreground">
              共 {trashTotal} 份，第 {page + 1} / {trashPageCount} 页
            </p>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={page === 0 || trashLoading}
                onClick={() => setPage((value) => value - 1)}
              >
                上一页
              </Button>
              <Select
                aria-label="跳转回收站页码"
                className="h-control-sm w-24"
                value={String(page + 1)}
                onChange={(event) => setPage(Number(event.target.value) - 1)}
                disabled={trashLoading}
              >
                {Array.from({ length: trashPageCount }, (_, index) => (
                  <option key={index + 1} value={index + 1}>
                    第 {index + 1} 页
                  </option>
                ))}
              </Select>
              <Button
                size="sm"
                variant="outline"
                disabled={page + 1 >= trashPageCount || trashLoading}
                onClick={() => setPage((value) => value + 1)}
              >
                下一页
              </Button>
            </div>
          </div>
        </Card>
        <Dialog
          open={Boolean(restoreTarget)}
          onOpenChange={(open) => {
            if (!open && !busyAction) {
              setRestoreTarget(null);
              setRestoreFolderId("");
              setRestoreConflict(null);
              setRestoreError(null);
            }
          }}
        >
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>恢复资料</DialogTitle>
              <DialogDescription>
                “{restoreTarget?.title}
                ”将恢复到资料库。已发布或发布失败的资料会恢复为“已确认”，重新发布后才会进入检索。
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-2 text-ui-sm">
                <dt className="text-muted-foreground">文件名</dt>
                <dd className="break-all">
                  {restoreTarget?.original_filename}
                </dd>
                <dt className="text-muted-foreground">原目录</dt>
                <dd className="break-words">
                  {restoreTarget?.category_path ||
                    restoreTarget?.category_label}
                </dd>
              </dl>
              <CategoryTreePicker
                categories={categories}
                value={restoreFolderId}
                currentCategoryId={restoreTarget?.category_id}
                currentCategorySelectable
                onChange={(categoryId) => {
                  setRestoreFolderId(categoryId);
                  setRestoreConflict(null);
                  setRestoreError(null);
                }}
                label="恢复到目录"
              />
              {!categories.some(
                (category) => category.id === restoreTarget?.category_id,
              ) && (
                <p
                  className="rounded-ui-md border border-warning/40 bg-warning/10 px-3 py-2 text-ui-sm"
                  role="status"
                >
                  原目录已停用，请选择其他有效目录。
                </p>
              )}
              {restoreConflict && (
                <div
                  className="space-y-2 rounded-ui-md border border-warning/50 bg-warning/10 p-3 text-ui-sm"
                  role="alert"
                >
                  <p className="font-medium">所选目录存在同名资料</p>
                  <p className="break-words">
                    {restoreConflict.title}（{restoreConflict.original_filename}
                    ）
                  </p>
                  <p className="text-muted-foreground">
                    可以选择其他目录；确认替换会将上述资料移入回收站。已发布资料会立即停止检索。
                  </p>
                </div>
              )}
              {restoreError && (
                <p
                  className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui-sm text-destructive"
                  role="alert"
                >
                  {restoreError}
                </p>
              )}
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                disabled={Boolean(busyAction)}
                onClick={() => setRestoreTarget(null)}
              >
                取消
              </Button>
              {restoreConflict &&
                (restoreConflict.has_published_head ||
                !["draft", "rejected"].includes(
                  restoreConflict.lifecycle_status,
                )
                  ? can("item.archive_published")
                  : can("item.archive_draft")) && (
                  <Button
                    variant="destructive"
                    disabled={Boolean(busyAction) || !restoreFolderId}
                    onClick={() => void restoreContent(true)}
                  >
                    {busyAction ? "替换中…" : "替换并恢复"}
                  </Button>
                )}
              <Button
                disabled={
                  Boolean(busyAction) ||
                  !restoreFolderId ||
                  Boolean(restoreConflict)
                }
                onClick={() => void restoreContent()}
              >
                {busyAction ? "恢复中…" : "确认恢复"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        <Dialog
          open={trashPreflightOpen}
          onOpenChange={(open) => {
            if (
              !open &&
              busyAction !== "bulk-restore" &&
              busyAction !== "restore-preflight"
            ) {
              setTrashPreflightOpen(false);
              setTrashPreflight([]);
              setTrashBulkTarget("original");
            }
          }}
        >
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>
                {trashPreflight.length > 0 ? "确认批量恢复" : "批量恢复"}
              </DialogTitle>
              <DialogDescription>
                {trashPreflight.length > 0
                  ? "系统已检查目录、同名冲突、版本和活动任务。只会恢复检查通过的资料。"
                  : `已选择 ${trashSelected.length} 份资料。选择恢复位置后，系统会先检查冲突和当前状态。`}
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <label className="block space-y-1.5 text-ui-sm font-medium">
                <span>恢复到</span>
                <Select
                  value={trashBulkTarget}
                  onChange={(event) => {
                    setTrashBulkTarget(event.target.value);
                    setTrashPreflight([]);
                  }}
                >
                  <option value="original">各自原目录</option>
                  {categories.map((category) => (
                    <option key={category.id} value={category.id}>
                      {category.full_path ||
                        `${category.display_code} ${category.display_name}`}
                    </option>
                  ))}
                </Select>
              </label>
              {trashPreflight.length > 0 && (
                <div className="space-y-3">
                  <p className="text-ui-sm">
                    可恢复{" "}
                    <strong>
                      {
                        trashPreflight.filter(
                          (entry) => entry.status === "ready",
                        ).length
                      }
                    </strong>{" "}
                    份，需处理{" "}
                    <strong>
                      {
                        trashPreflight.filter(
                          (entry) => entry.status !== "ready",
                        ).length
                      }
                    </strong>{" "}
                    份。
                  </p>
                  <ul className="max-h-72 divide-y divide-border overflow-y-auto border-y border-border">
                    {trashPreflight.map((entry) => {
                      const target = trashItems.find(
                        (item) => item.version_id === entry.version_id,
                      );
                      return (
                        <li
                          key={entry.item_id}
                          className="flex items-start justify-between gap-3 py-2 text-ui-sm"
                        >
                          <span className="min-w-0 break-words">
                            {target?.title || "资料"}
                            <span className="mt-0.5 block text-ui-xs text-muted-foreground">
                              {entry.target_category_path || "目标目录不可用"}
                            </span>
                          </span>
                          <Badge
                            variant={
                              entry.status === "ready" ? "success" : "warning"
                            }
                          >
                            {entry.message}
                          </Badge>
                        </li>
                      );
                    })}
                  </ul>
                  {trashPreflight.some(
                    (entry) => entry.status === "conflict",
                  ) && (
                    <p className="text-ui-xs text-muted-foreground">
                      同名冲突不会自动替换，请关闭后使用单项“恢复”处理。
                    </p>
                  )}
                </div>
              )}
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                disabled={
                  busyAction === "bulk-restore" ||
                  busyAction === "restore-preflight"
                }
                onClick={() => {
                  setTrashPreflightOpen(false);
                  setTrashPreflight([]);
                  setTrashBulkTarget("original");
                }}
              >
                取消
              </Button>
              {trashPreflight.length === 0 ? (
                <Button
                  disabled={busyAction === "restore-preflight"}
                  onClick={() => void preflightBulkRestore()}
                >
                  {busyAction === "restore-preflight"
                    ? "检查中…"
                    : "检查恢复条件"}
                </Button>
              ) : (
                <Button
                  disabled={
                    busyAction === "bulk-restore" ||
                    !trashPreflight.some((entry) => entry.status === "ready")
                  }
                  onClick={() => void bulkRestoreTrash()}
                >
                  {busyAction === "bulk-restore"
                    ? "恢复中…"
                    : "恢复检查通过的资料"}
                </Button>
              )}
            </DialogFooter>
          </DialogContent>
        </Dialog>
        <Dialog
          open={trashPurgeOpen}
          onOpenChange={(open) => {
            if (!open && busyAction !== "trash-purge") {
              setTrashPurgeOpen(false);
              setTrashPurgePreflight(null);
              setTrashPurgeConfirmation("");
            }
          }}
        >
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>永久删除资料</DialogTitle>
              <DialogDescription>
                此操作不可撤销，将同时删除资料文件、视频、转录产物和检索索引。完成后只能从事先创建的备份恢复。
              </DialogDescription>
            </DialogHeader>
            {trashPurgePreflight && (
              <div className="space-y-4">
                <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-2 text-ui-sm">
                  <dt className="text-muted-foreground">可删除</dt>
                  <dd>{trashPurgePreflight.ready_count} 份资料</dd>
                  <dt className="text-muted-foreground">文件大小</dt>
                  <dd>
                    {formatUploadSize(trashPurgePreflight.total_size_bytes)}
                  </dd>
                  <dt className="text-muted-foreground">视频文件</dt>
                  <dd>{trashPurgePreflight.media_count} 个</dd>
                  <dt className="text-muted-foreground">转录版本</dt>
                  <dd>{trashPurgePreflight.transcript_version_count} 个</dd>
                  <dt className="text-muted-foreground">转录产物</dt>
                  <dd>{trashPurgePreflight.artifact_count} 个</dd>
                  <dt className="text-muted-foreground">索引任务记录</dt>
                  <dd>{trashPurgePreflight.index_job_count} 条</dd>
                  <dt className="text-muted-foreground">已阻止</dt>
                  <dd>{trashPurgePreflight.blocked_count} 份</dd>
                </dl>
                {trashPurgePreflight.media_count > 0 && (
                  <p
                    className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui-sm text-destructive"
                    role="alert"
                  >
                    所选内容包含视频。永久删除会清除视频原文件、已准备的音频、全部转录版本及其检索数据。
                  </p>
                )}
                {trashPurgePreflight.blocked_count > 0 && (
                  <ul className="max-h-40 divide-y divide-border overflow-y-auto border-y border-border">
                    {trashPurgePreflight.items
                      .filter((item) => item.status === "blocked")
                      .map((item) => (
                        <li
                          key={item.item_id}
                          className="flex justify-between gap-3 py-2 text-ui-sm"
                        >
                          <span className="break-words">{item.title}</span>
                          <span className="text-destructive">
                            {item.reason}
                          </span>
                        </li>
                      ))}
                  </ul>
                )}
                <label className="block space-y-1.5 text-ui-sm font-medium">
                  <span>
                    输入“{trashPurgePreflight.confirmation_phrase}”确认
                  </span>
                  <Input
                    value={trashPurgeConfirmation}
                    onChange={(event) =>
                      setTrashPurgeConfirmation(event.target.value)
                    }
                    autoComplete="off"
                  />
                </label>
              </div>
            )}
            <DialogFooter>
              <Button
                variant="outline"
                disabled={busyAction === "trash-purge"}
                onClick={() => setTrashPurgeOpen(false)}
              >
                取消
              </Button>
              <Button
                variant="destructive"
                disabled={
                  !trashPurgePreflight ||
                  trashPurgePreflight.ready_count === 0 ||
                  trashPurgeConfirmation !==
                    trashPurgePreflight.confirmation_phrase ||
                  busyAction === "trash-purge"
                }
                onClick={() => void confirmTrashPurge()}
              >
                {busyAction === "trash-purge" ? "永久删除中…" : "永久删除"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        <Dialog
          open={trashSettingsOpen}
          onOpenChange={(open) => {
            if (!open && busyAction !== "trash-settings-save")
              setTrashSettingsOpen(false);
          }}
        >
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>回收站清理策略</DialogTitle>
              <DialogDescription>
                自动清理仅处理超过保留期限的资料；关闭时仍可通过多选手动永久删除。
              </DialogDescription>
            </DialogHeader>
            {trashSettings && (
              <div className="space-y-5">
                <label className="flex items-center justify-between gap-4 border-y border-border py-3 text-ui-sm">
                  <span>
                    <span className="block font-medium">自动清理</span>
                    <span className="mt-1 block text-ui-xs text-muted-foreground">
                      当前状态：
                      {trashSettings.cleanup_enabled ? "已启用" : "已关闭"}
                    </span>
                  </span>
                  <Checkbox
                    aria-label="启用自动清理"
                    checked={trashSettings.cleanup_enabled}
                    onChange={(event) =>
                      setTrashSettings({
                        ...trashSettings,
                        cleanup_enabled: event.target.checked,
                      })
                    }
                  />
                </label>
                <div className="grid gap-4 sm:grid-cols-3">
                  <label className="space-y-1.5 text-ui-sm font-medium">
                    <span>保留天数</span>
                    <Input
                      type="number"
                      min={1}
                      max={3650}
                      value={trashSettings.retention_days}
                      onChange={(event) =>
                        setTrashSettings({
                          ...trashSettings,
                          retention_days: Number(event.target.value),
                        })
                      }
                    />
                  </label>
                  <label className="space-y-1.5 text-ui-sm font-medium">
                    <span>到期提醒天数</span>
                    <Input
                      type="number"
                      min={0}
                      max={365}
                      value={trashSettings.warning_days}
                      onChange={(event) =>
                        setTrashSettings({
                          ...trashSettings,
                          warning_days: Number(event.target.value),
                        })
                      }
                    />
                  </label>
                  <label className="space-y-1.5 text-ui-sm font-medium">
                    <span>单批上限</span>
                    <Input
                      type="number"
                      min={1}
                      max={20}
                      value={trashSettings.batch_limit}
                      onChange={(event) =>
                        setTrashSettings({
                          ...trashSettings,
                          batch_limit: Number(event.target.value),
                        })
                      }
                    />
                  </label>
                </div>
                <div>
                  <h3 className="text-ui-sm font-medium">最近清理记录</h3>
                  {trashPurgeRuns.length === 0 ? (
                    <p className="mt-2 text-ui-xs text-muted-foreground">
                      暂无清理记录
                    </p>
                  ) : (
                    <ul className="mt-2 max-h-44 divide-y divide-border overflow-y-auto border-y border-border">
                      {trashPurgeRuns.map((run) => (
                        <li
                          key={run.id}
                          className="flex items-center justify-between gap-3 py-2 text-ui-sm"
                        >
                          <span>
                            {run.trigger_type === "automatic"
                              ? "自动清理"
                              : "手动清理"}{" "}
                            · {run.actor_name || "系统"}
                            <span className="mt-0.5 block text-ui-xs text-muted-foreground">
                              {formatAdminDate(run.created_at)}
                            </span>
                          </span>
                          <Badge
                            variant={run.failed_count ? "warning" : "success"}
                          >
                            {run.succeeded_count} 成功 / {run.failed_count} 失败
                          </Badge>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            )}
            <DialogFooter>
              <Button
                variant="outline"
                disabled={busyAction === "trash-settings-save"}
                onClick={() => setTrashSettingsOpen(false)}
              >
                取消
              </Button>
              <Button
                disabled={
                  !trashSettings ||
                  trashSettings.warning_days >= trashSettings.retention_days ||
                  busyAction === "trash-settings-save"
                }
                onClick={() => void saveTrashSettings()}
              >
                {busyAction === "trash-settings-save" ? "保存中…" : "保存策略"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        {auditDialog}
      </section>
    );
  }

  return (
    <section className="space-y-5" aria-labelledby="managed-content-title">
      {activeUpload && <div className="fixed right-4 top-4 z-50 w-[min(24rem,calc(100vw-2rem))] rounded-ui-lg border border-primary/40 bg-background px-4 py-3 shadow-surface" role="status" aria-live="polite"><div className="flex items-start justify-between gap-3"><div><p className="font-medium">{activeUpload.phase === "processing" ? "服务端处理中…" : activeUpload.phase === "completed" ? "上传完成" : activeUpload.phase === "failed" ? "上传失败" : "上传中"}</p><p className="mt-1 text-ui-xs text-muted-foreground">{activeUpload.totalFiles} 个文件 · {activeUpload.targetPath}</p>{activeUpload.message && <p className="mt-1 text-ui-xs">{activeUpload.message}</p>}</div><button type="button" className="text-ui-xs text-muted-foreground" aria-label="关闭上传提示" onClick={() => setActiveUpload(null)}>关闭</button></div></div>}
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-ui-xs font-medium text-primary">内容管理</p>
          <h1
            id="managed-content-title"
            className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground"
          >
            资料管理
          </h1>
          <p className="mt-1 text-ui-sm text-muted-foreground">
            统一管理资料的上传、分类、确认和发布。
          </p>
        </div>
      </header>

      {viewTabs}

      <section
        className="grid grid-cols-2 gap-3 lg:grid-cols-4"
        aria-label="资料状态概览"
      >
        {[
          { label: "全部资料", value: Object.values(counts).reduce((sum, value) => sum + value, 0), icon: <FileText className="size-4" /> },
          { label: "待确认", value: counts.awaiting_review || 0, icon: <AlertTriangle className="size-4" />, tone: "warning" as const },
          { label: "已确认", value: counts.approved || 0, icon: <CheckCircle2 className="size-4" />, tone: "success" as const },
          { label: "已发布", value: counts.published || 0, icon: <Rocket className="size-4" />, tone: "success" as const },
        ].map((summary) => <ManagedSummaryCard key={summary.label} label={summary.label} value={summary.value} icon={summary.icon} tone={summary.tone} />)}
      </section>

      {!enabled && !loading && (
        <div
          className="border border-warning/40 bg-warning/10 px-4 py-3 text-ui-sm"
          role="status"
        >
          资料管理当前未启用，上传和流程操作暂不可用。
        </div>
      )}
      {error && (
        <ErrorState
          title="资料列表加载失败"
          description={error}
          action={
            <Button size="sm" variant="outline" onClick={() => void load()}>
              重新加载
            </Button>
          }
        />
      )}

      {can("folder.review") && folderRequests.length > 0 && (
        <Card
          className="overflow-hidden shadow-surface"
          aria-labelledby="folder-requests-title"
        >
          <div className="border-b border-border px-4 py-3 sm:px-5">
            <h2
              id="folder-requests-title"
              className="text-ui-base font-semibold"
            >
              待处理目录申请
            </h2>
          </div>
          <ul className="divide-y divide-border">
            {folderRequests.map((request) => (
              <li
                key={request.id}
                className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5"
              >
                <div className="min-w-0">
                  <p className="break-words text-ui-sm font-medium">
                    {request.display_name}
                  </p>
                  <p className="mt-0.5 text-ui-xs text-muted-foreground">
                    上级目录：{request.parent_label} · 申请人：
                    {request.requester_name || "未知"}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busyAction === `folder-request:${request.id}`}
                    onClick={() => void reviewFolder(request, false)}
                  >
                    <X className="size-4" />
                    退回
                  </Button>
                  <Button
                    size="sm"
                    disabled={busyAction === `folder-request:${request.id}`}
                    onClick={() => void reviewFolder(request, true)}
                  >
                    <Check className="size-4" />
                    批准
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <Card
        className="shadow-surface [&_table]:!min-w-[56rem]"
        aria-labelledby="managed-list-title"
      >
        <div className="grid gap-3 border-b border-border px-4 py-4 xl:grid-cols-[minmax(13rem,1fr)_18rem_auto] xl:items-end min-[1400px]:grid-cols-[minmax(13rem,1fr)_24rem_auto] sm:px-5">
          <div className="min-w-0">
            <h2 id="managed-list-title" className="text-ui-base font-semibold">
              资料列表
            </h2>
            <p className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-ui-xs text-muted-foreground">
              <span>共 {total} 份</span>
              <span role="status" aria-live="polite">
                ·{" "}
                {selectedFolders.length > 0 ? (
                  <>
                    已选择 <strong>{selectedFolders.length}</strong> 个文件夹、
                    <strong>{selected.length}</strong> 份资料
                  </>
                ) : selected.length > 0 ? (
                  <>
                  已选择 <strong>{selected.length}</strong> 份
                  </>
                ) : (
                  <>未选择资料</>
                )}
              </span>
            </p>
          </div>
          <ManagedContentSearchFilters
            queryInput={queryInput}
            searchScope={searchScope}
            currentDirectoryAvailable={Boolean(currentFolderId)}
            statusFilter={statusFilter}
            sourceFilter={sourceFilter}
            kindFilter={kindFilter}
            onQueryInputChange={setQueryInput}
            onSearchScopeChange={setSearchScope}
            onStatusFilterChange={setStatusFilter}
            onSourceFilterChange={setSourceFilter}
            onKindFilterChange={setKindFilter}
            onClear={() => {
              setQueryInput("");
              setStatusFilter("");
              setSourceFilter("");
              setKindFilter("");
            }}
          />
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {can("item.upload") && (
              <Button
                size="sm"
                className="max-sm:h-control-md"
                onClick={openUploadDialog}
                disabled={
                  !enabled ||
                  !currentFolderId ||
                  uploading ||
                  uploadChecking ||
                  folderScanning
                }
              >
                <Upload className="size-4" />
                {folderScanning ? "读取文件夹中…" : "上传文件"}
              </Button>
            )}
            {isSystemAdmin && currentFolderId && (
              <Button
                size="sm"
                variant="outline"
                className="max-sm:h-control-md"
                onClick={() => openTranscriptionDialog([], "category")}
                disabled={loading || refreshing}
              >
                <Rocket className="size-4" />
                批量转录本目录
              </Button>
            )}
            {isSystemAdmin &&
              activeUpload?.batchId &&
              activeUpload.phase === "completed" && (
                <Button
                  size="sm"
                  variant="outline"
                  className="max-sm:h-control-md"
                  onClick={() => openTranscriptionDialog([], "batch")}
                  disabled={loading || refreshing}
                >
                  <Rocket className="size-4" />
                  转录最近上传批次
                </Button>
              )}
            <Button
              size="sm"
              variant="outline"
              className="max-sm:h-control-md"
              onClick={() => void load(true)}
              disabled={loading || refreshing}
            >
              <RefreshCw
                className={refreshing ? "size-4 animate-spin" : "size-4"}
              />
              {refreshing ? "刷新中…" : "刷新列表"}
            </Button>
            {totalSelectedCount > 1 || selectedFolders.length > 0 ? (
              <BatchActionsMenu
                disabled={bulkDisabled}
                options={
                  selectedFolders.length > 0
                    ? [
                        {
                          key: "move",
                          label: "批量调整目录",
                          icon: <FolderInput className="size-4" />,
                          disabled: !can("category.manage"),
                          disabledReason: "需要目录管理权限",
                          onSelect: () => openRecursiveBulk("move"),
                        },
                        {
                          key: "publish",
                          label: "批量发布",
                          icon: <Rocket className="size-4" />,
                          disabled: !can("item.publish"),
                          onSelect: () => openRecursiveBulk("publish"),
                        },
                        {
                          key: "download",
                          label: "批量下载",
                          icon: <Download className="size-4" />,
                          disabled: !can("item.download"),
                          onSelect: () => openRecursiveBulk("download"),
                        },
                        {
                          key: "delete",
                          label: "批量删除文件夹",
                          icon: <Trash2 className="size-4" />,
                          disabled: !can("category.manage"),
                          destructive: true,
                          onSelect: () => openRecursiveBulk("delete"),
                        },
                        ...(can("category.force_delete") && can("trash.purge")
                          ? [
                              {
                                key: "force-delete",
                                label: "强制永久删除文件夹",
                                icon: <Trash2 className="size-4" />,
                                destructive: true,
                                onSelect: () =>
                                  openRecursiveBulk("force_delete" as const),
                              },
                            ]
                          : []),
                      ]
                    : [
                        {
                          key: "start-transcription",
                          label: "批量开始转录",
                          icon: <Rocket className="size-4" />,
                          disabled: !hasTranscribableSelection,
                          disabledReason: "请选择待转录视频",
                          onSelect: () =>
                            openTranscriptionDialog(selectedItems),
                        },
                        {
                          key: "move",
                          label: bulkMoveLabel,
                          icon: <FolderInput className="size-4" />,
                          disabled: !hasMovableSelection,
                          disabledReason:
                            "所选资料必须属于同一状态且都可调整目录",
                          onSelect: () => {
                            setBulkFailures([]);
                            setBulkMoveFolderId("");
                            setBulkNote("");
                            setBulkAction("move");
                          },
                        },
                        {
                          key: "publish",
                          label: "批量发布",
                          icon: <Rocket className="size-4" />,
                          disabled:
                            !can("item.publish") || !hasPublishableSelection,
                          onSelect: () => {
                            setBulkFailures([]);
                            setBulkNote("");
                            setBulkAction("publish");
                          },
                        },
                        {
                          key: "download",
                          label: "批量下载",
                          icon: <Download className="size-4" />,
                          disabled: !hasDownloadableSelection,
                          onSelect: () => {
                            void downloadSelected();
                          },
                        },
                        {
                          key: "archive",
                          label: "批量删除",
                          icon: <Trash2 className="size-4" />,
                          disabled: !hasDeletableSelection,
                          destructive: true,
                          onSelect: () => openDeleteDialog(selectedItems),
                        },
                      ]
                }
              />
            ) : (
              (can("folder.request") || can("category.manage")) && (
                <Button
                  size="sm"
                  variant="outline"
                  className="max-sm:h-control-md"
                  onClick={() =>
                    can("category.manage")
                      ? setNewFolderOpen(true)
                      : setRequestFolderOpen(true)
                  }
                  disabled={!currentFolder}
                >
                  <FolderPlus className="size-4" />
                  新建目录
                </Button>
              )
            )}
          </div>
        </div>
        <div
          className="border-b border-border bg-surface-muted/40 px-4 py-3 sm:px-5"
          data-testid="managed-folder-address"
        >
          <div className="flex min-w-0 items-center gap-2">
            <nav
              className="flex min-w-0 flex-1 items-center gap-1 overflow-hidden rounded-ui-md border border-input bg-background px-3 py-2 text-ui-sm"
              aria-label="资料路径"
            >
              <button
                type="button"
                className="shrink-0 rounded px-1 py-0.5 font-medium hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => navigateToFolder("")}
              >
                /
              </button>
              {breadcrumbs.map((folder) => (
                <span
                  key={folder.id}
                  className="flex min-w-0 items-center gap-1"
                >
                  <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                  <button
                    type="button"
                    className="max-w-56 truncate rounded px-1 py-0.5 hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    onClick={() => navigateToFolder(folder.id)}
                  >
                    {folder.display_code} {folder.display_name}
                  </button>
                </span>
              ))}
            </nav>
            <IconButton
              label="返回上一目录"
              tooltip={currentFolder ? "返回上一目录" : "当前已在根目录"}
              className="size-control-md border border-border bg-background"
              disabled={!currentFolder}
              onClick={() => navigateToFolder(currentFolder?.parent_id || "")}
            >
              <ArrowUp className="size-4" />
            </IconButton>
          </div>
        </div>

        <div
          data-testid="managed-content-drop-list"
          className="relative"
          onDragEnter={handleListDragEnter}
          onDragOver={handleListDragOver}
          onDragLeave={handleListDragLeave}
          onDrop={handleListDrop}
        >
          {listDropActive && (
            <div
              data-testid="managed-content-drop-overlay"
              className="pointer-events-none absolute inset-1 z-sticky rounded-ui-lg border-2 border-dashed border-primary/70 bg-background/70 text-center shadow-focus backdrop-blur-[1px]"
              role="status"
              aria-live="polite"
            >
              <div
                className="absolute left-1/2 flex w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-3"
                style={{ top: listDropPromptTop }}
              >
                <span
                  className="flex size-12 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-surface"
                  aria-hidden="true"
                >
                  <Upload className="size-6" />
                </span>
                <div className="space-y-1">
                  <p className="break-words text-ui-base font-semibold">
                    松开以上传文件到“{currentFolderDropLabel}”
                  </p>
                  <p className="text-ui-xs text-muted-foreground">
                    也支持拖入文件夹；支持 PDF、Markdown、Word、Excel、PPT 和
                    XMind 文件
                    {isSystemAdmin ? "；MP4 视频上传后显示为待转录资料" : ""}
                  </p>
                </div>
              </div>
            </div>
          )}
          {loading ? (
            <LoadingState
              className="min-h-48 border-x-0 border-b-0"
              label="正在加载资料…"
            />
          ) : !error && items.length === 0 && sortedChildFolders.length === 0 ? (
            <EmptyState
              className="min-h-56 rounded-none border-x-0 border-b-0 sm:min-h-64"
              title="没有符合条件的资料"
              description="请调整筛选条件或上传新资料。"
            />
          ) : (
            !error && (
              <>
                <div className="hidden overflow-x-auto border-t border-border lg:block">
                  <table className="w-full min-w-[72rem] text-ui-sm">
                    <thead className="border-b border-border bg-surface-muted text-left text-muted-foreground">
                      <tr>
                        <th className="w-8 px-1.5 py-3">
                          <Checkbox
                            aria-label="选择当前页资料"
                            checked={allSelected}
                            onChange={toggleAll}
                          />
                        </th>
                        {(
                          [
                            ["docType", "类型"],
                            ["title", "资料"],
                            ["updatedAt", "更新时间"],
                            ["status", "状态"],
                            ["source", "来源"],
                          ] as [SortKey, string][]
                        ).map(([key, label]) => (
                          <th
                            key={key}
                            aria-sort={
                              sort?.key === key
                                ? sort.direction === "asc"
                                  ? "ascending"
                                  : "descending"
                                : "none"
                            }
                            className={
                              key === "docType"
                                ? "w-16 px-1 py-3 text-center font-medium"
                                : key === "title"
                                  ? "px-1.5 py-3 font-medium"
                                  : "px-3 py-3 font-medium"
                            }
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
                        <th className="px-3 py-3 text-right font-medium">
                          操作
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {sortedChildFolders.map((folder) => {
                        const folderLabel = `${folder.display_code} ${folder.display_name}`;
                        return (
                          <tr
                            key={folder.id}
                            data-testid={`managed-folder-row-${folder.id}`}
                            className={`cursor-pointer transition-colors duration-normal hover:bg-surface-muted/60 ${selectedFolders.includes(folder.id) || draggedItem ? "bg-primary/5" : ""} ${draggedItem ? "outline outline-1 -outline-offset-1 outline-primary/50" : ""}`}
                            onClick={() => navigateToFolder(folder.id)}
                            onDragOver={(event) => {
                              if (draggedItem) {
                                event.preventDefault();
                                event.stopPropagation();
                              }
                            }}
                            onDrop={(event) => {
                              if (!draggedItem) return;
                              event.preventDefault();
                              event.stopPropagation();
                              void moveItemTo(draggedItem, folder.id);
                            }}
                          >
                            <td className="px-1.5 py-3">
                              <Checkbox
                                aria-label={`选择文件夹${folderLabel}`}
                                checked={selectedFolders.includes(folder.id)}
                                disabled={false}
                                onClick={(event) => event.stopPropagation()}
                                onChange={() =>
                                  toggleFolderSelection(folder.id)
                                }
                              />
                            </td>
                            <td className="px-1 py-3">
                              <ManagedItemType folder compact />
                            </td>
                            <td className="max-w-xs px-1.5 py-3">
                              <button
                                type="button"
                                className="block max-w-full rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                onClick={() => navigateToFolder(folder.id)}
                              >
                                <span className="block break-words font-medium">
                                  {folderLabel}
                                </span>
                                <span className="mt-0.5 block text-ui-xs text-muted-foreground">
                                  {folderContentSummary(folder)}
                                </span>
                              </button>
                            </td>
                            <td className="whitespace-nowrap px-3 py-3 tabular-nums">
                              {formatManagedUpdatedAt(folder.updated_at)}
                            </td>
                            <td className="px-3 py-3 text-muted-foreground">
                              —
                            </td>
                            <td className="px-3 py-3 text-muted-foreground">
                              —
                            </td>
                            <td className="px-3 py-3 text-right">
                              {renderFolderActions(folder)}
                            </td>
                          </tr>
                        );
                      })}
                      {sortedItems.map((item, index) => {
                        const movable = canMoveItem(item);
                        const draggable =
                          movable && moveOperation(item) !== "reclassify";
                        const rowSelectable = canSelectItem(item);
                        return (
                          <tr
                            key={item.item_id}
                            draggable={draggable}
                            title={
                              draggable ? "拖动到文件夹行可调整目录" : undefined
                            }
                            onDragStart={() => setDraggedItem(item)}
                            onDragEnd={() => setDraggedItem(null)}
                            className={`transition-colors duration-normal hover:bg-surface-muted/60 ${draggable ? "cursor-grab" : ""}`}
                          >
                            <td className="px-1.5 py-3">
                              <Checkbox
                                aria-label={`选择${item.title}`}
                                checked={selected.includes(item.version_id)}
                                disabled={!rowSelectable}
                                title={
                                  !rowSelectable
                                    ? "当前状态不可加入批量操作"
                                    : undefined
                                }
                                onChange={() =>
                                  setSelected((current) =>
                                    current.includes(item.version_id)
                                      ? current.filter(
                                          (id) => id !== item.version_id,
                                        )
                                      : [...current, item.version_id],
                                  )
                                }
                              />
                            </td>
                            <td className="px-1 py-3">
                              <ManagedItemType
                                docType={item.doc_type}
                                compact
                              />
                            </td>
                            <td className="max-w-xs px-1.5 py-3">
                              <ManagedItemIdentity
                                item={item}
                                showCategoryPath={showGlobalResults}
                              />
                            </td>
                            <td className="whitespace-nowrap px-3 py-3 tabular-nums">
                              {formatManagedUpdatedAt(item.updated_at)}
                            </td>
                            <td className="px-3 py-3">
                              {renderItemStatus(item)}
                            </td>
                            <td className="px-3 py-3">
                              {sourceLabel[item.source_origin] || "其他来源"}
                            </td>
                            <td className="px-3 py-3 text-right">
                              {renderActions(item)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <ul className="divide-y divide-border border-t border-border lg:hidden">
                  {sortedChildFolders.map((folder) => {
                    const folderLabel = `${folder.display_code} ${folder.display_name}`;
                    return (
                      <li
                        key={folder.id}
                        data-testid={`managed-folder-mobile-${folder.id}`}
                        className={`min-h-16 space-y-2 px-4 py-3 sm:px-5 ${selectedFolders.includes(folder.id) ? "bg-primary/5" : ""}`}
                      >
                        <div className="flex items-start gap-3">
                          <Checkbox
                            className="mt-1"
                            aria-label={`选择文件夹${folderLabel}`}
                            checked={selectedFolders.includes(folder.id)}
                            disabled={false}
                            onChange={() => toggleFolderSelection(folder.id)}
                          />
                          <button
                            type="button"
                            className="flex min-w-0 flex-1 items-center gap-3 rounded-ui-md text-left transition-colors hover:bg-surface-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            onClick={() => navigateToFolder(folder.id)}
                          >
                            <Folder
                              className="size-5 shrink-0 text-primary"
                              aria-hidden="true"
                            />
                            <span className="min-w-0 flex-1">
                              <span className="block break-words font-medium">
                                {folderLabel}
                              </span>
                              <span className="mt-0.5 block text-ui-xs text-muted-foreground">
                                {folderContentSummary(folder)}
                              </span>
                            </span>
                          </button>
                        </div>
                        {renderFolderActions(folder)}
                      </li>
                    );
                  })}
                  {sortedItems.map((item, index) => {
                    const rowSelectable = canSelectItem(item);
                    return (
                      <li
                        key={item.item_id}
                        className="space-y-3 px-4 py-4 sm:px-5"
                      >
                        <div className="flex items-start gap-2">
                          <Checkbox
                            className="mt-0.5"
                            aria-label={`选择${item.title}`}
                            checked={selected.includes(item.version_id)}
                            disabled={!rowSelectable}
                            title={
                              !rowSelectable
                                ? "当前状态不可加入批量操作"
                                : undefined
                            }
                            onChange={() =>
                              setSelected((current) =>
                                current.includes(item.version_id)
                                  ? current.filter(
                                      (id) => id !== item.version_id,
                                    )
                                  : [...current, item.version_id],
                              )
                            }
                          />
                          <ManagedItemType docType={item.doc_type} />
                          <div className="min-w-0 flex-1">
                            <ManagedItemIdentity
                              item={item}
                              showCategoryPath={showGlobalResults}
                            />
                          </div>
                        </div>
                        <dl className="grid grid-cols-[4rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-ui-sm">
                          <dt className="text-muted-foreground">状态</dt>
                          <dd>{renderItemStatus(item)}</dd>
                          <dt className="text-muted-foreground">更新时间</dt>
                          <dd className="whitespace-nowrap tabular-nums">
                            {formatManagedUpdatedAt(item.updated_at)}
                          </dd>
                          <dt className="text-muted-foreground">来源</dt>
                          <dd>
                            {sourceLabel[item.source_origin] || "其他来源"}
                          </dd>
                        </dl>
                        {renderActions(item)}
                      </li>
                    );
                  })}
                </ul>
                <div className="flex flex-col gap-2 border-t border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5">
                  <p className="text-ui-xs text-muted-foreground">
                    共 {total} 份，第 {page + 1} / {pageCount} 页
                  </p>
                  <div className="flex flex-wrap items-center justify-end gap-2">
                    <label className="flex items-center gap-2 text-ui-xs text-muted-foreground">
                      每页
                      <Select
                        aria-label="每页条数"
                        className="h-control-sm w-20"
                        value={String(pageSize)}
                        onChange={(event) =>
                          setPageSize(Number(event.target.value))
                        }
                      >
                        {PAGE_SIZE_OPTIONS.map((size) => (
                          <option key={size} value={size}>
                            {size} 条
                          </option>
                        ))}
                      </Select>
                    </label>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={page === 0 || loading}
                      onClick={() => setPage((value) => value - 1)}
                    >
                      上一页
                    </Button>
                    <Select
                      aria-label="跳转页码"
                      className="h-control-sm w-24"
                      value={String(page + 1)}
                      onChange={(event) =>
                        setPage(Number(event.target.value) - 1)
                      }
                      disabled={loading}
                    >
                      {Array.from({ length: pageCount }, (_, index) => (
                        <option key={index + 1} value={index + 1}>
                          第 {index + 1} 页
                        </option>
                      ))}
                    </Select>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={page + 1 >= pageCount || loading}
                      onClick={() => setPage((value) => value + 1)}
                    >
                      下一页
                    </Button>
                  </div>
                </div>
              </>
            )
          )}
        </div>
      </Card>

      <Dialog
        open={Boolean(folderDetailTarget)}
        onOpenChange={(open) => {
          if (!open) setFolderDetailTarget(null);
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              {folderDetailTarget
                ? `${folderDetailTarget.display_code} ${folderDetailTarget.display_name}`
                : "文件夹详情"}
            </DialogTitle>
            <DialogDescription>
              查看文件夹路径、编号、层级和目录内容统计。
            </DialogDescription>
          </DialogHeader>
          {folderDetailTarget && (
            <div className="space-y-4">
              <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-2 text-ui-sm [&_dt]:whitespace-nowrap">
                <dt className="text-muted-foreground">完整路径</dt>
                <dd className="break-words">{folderDetailTarget.full_path}</dd>
                <dt className="text-muted-foreground">当前编号</dt>
                <dd>{folderDetailTarget.display_code}</dd>
                <dt className="text-muted-foreground">层级</dt>
                <dd>第 {folderDetailTarget.level} 级</dd>
                <dt className="text-muted-foreground">状态</dt>
                <dd>
                  {folderDetailTarget.is_active ? (
                    <Badge variant="success">启用</Badge>
                  ) : (
                    <Badge variant="secondary">停用</Badge>
                  )}
                </dd>
                <dt className="text-muted-foreground">直接资料</dt>
                <dd>{folderDetailTarget.item_count} 份</dd>
                <dt className="text-muted-foreground">直接子文件夹</dt>
                <dd>{folderDetailTarget.direct_child_count ?? 0} 个</dd>
                <dt className="text-muted-foreground">全部子文件夹</dt>
                <dd>
                  {folderDetailTarget.total_child_count ??
                    folderDetailTarget.direct_child_count ??
                    0}{" "}
                  个
                </dd>
                <dt className="text-muted-foreground">全部资料</dt>
                <dd>
                  {folderDetailTarget.total_item_count ??
                    folderDetailTarget.item_count}{" "}
                  份
                </dd>
                <dt className="text-muted-foreground">创建时间</dt>
                <dd>{formatAdminDate(folderDetailTarget.created_at)}</dd>
                <dt className="text-muted-foreground">最后更新时间</dt>
                <dd>{formatAdminDate(folderDetailTarget.updated_at)}</dd>
              </dl>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  onClick={() => {
                    navigateToFolder(folderDetailTarget.id);
                    setFolderDetailTarget(null);
                  }}
                >
                  <Folder className="size-4" />
                  打开文件夹
                </Button>
                <Button
                  variant="outline"
                  disabled={
                    !can("item.download") ||
                    (folderDetailTarget.total_item_count ??
                      folderDetailTarget.item_count) < 1 ||
                    Boolean(busyAction)
                  }
                  onClick={() => void downloadFolder(folderDetailTarget)}
                >
                  <Download className="size-4" />
                  打包下载
                </Button>
                {can("category.manage") && (
                  <Button
                    variant="outline"
                    disabled={Boolean(busyAction)}
                    onClick={() => {
                      const target = folderDetailTarget;
                      setFolderDetailTarget(null);
                      openFolderNumber(target);
                    }}
                  >
                    <ListOrdered className="size-4" />
                    调整编号
                  </Button>
                )}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(folderNumberTarget)}
        onOpenChange={(open) => {
          if (!open && !busyAction?.startsWith("folder:")) {
            setFolderNumberTarget(null);
            setFolderNumberConfirming(false);
            setFolderActionError(null);
          }
        }}
      >
        <DialogContent>
          {!folderNumberConfirming ? (
            <>
              <DialogHeader>
                <DialogTitle>调整文件夹编号</DialogTitle>
                <DialogDescription>
                  编号同时决定同级文件夹顺序，并显示在文件夹名称和地址栏中。
                </DialogDescription>
              </DialogHeader>
              {folderNumberTarget && (
                <div className="space-y-4">
                  <div className="rounded-ui-md border border-border bg-surface-muted/40 px-3 py-2 text-ui-sm">
                    <p className="text-ui-xs text-muted-foreground">
                      当前文件夹
                    </p>
                    <p className="mt-1 break-words font-medium">
                      {folderNumberTarget.full_path}
                    </p>
                  </div>
                  <label className="block space-y-1.5 text-ui-sm font-medium">
                    <span>目标编号</span>
                    <Input
                      type="number"
                      min={1}
                      max={folderNumberSiblings.length}
                      step={1}
                      value={folderNumberValue}
                      onChange={(event) => {
                        setFolderNumberValue(event.target.value);
                        setFolderActionError(null);
                      }}
                      aria-label="目标编号"
                    />
                    <span className="block text-ui-xs font-normal text-muted-foreground">
                      可填写 1 到 {folderNumberSiblings.length}
                      ；调整后系统会保持同级编号连续。
                    </span>
                  </label>
                  {!folderNumberValid && (
                    <p className="text-ui-sm text-destructive" role="alert">
                      请输入 1 到 {folderNumberSiblings.length} 之间的整数。
                    </p>
                  )}
                  {folderActionError && (
                    <p className="text-ui-sm text-destructive" role="alert">
                      {folderActionError}
                    </p>
                  )}
                </div>
              )}
              <DialogFooter>
                <Button
                  variant="outline"
                  onClick={() => setFolderNumberTarget(null)}
                  disabled={
                    busyAction === `folder:${folderNumberTarget?.id}:number`
                  }
                >
                  取消
                </Button>
                <Button
                  onClick={() => setFolderNumberConfirming(true)}
                  disabled={
                    !folderNumberValid ||
                    parsedFolderNumber === currentFolderNumber ||
                    busyAction === `folder:${folderNumberTarget?.id}:number`
                  }
                >
                  下一步
                </Button>
              </DialogFooter>
            </>
          ) : (
            <>
              <DialogHeader>
                <DialogTitle>确认调整编号</DialogTitle>
                <DialogDescription>
                  目标编号当前已被占用，继续后将整体调整同级文件夹编号。
                </DialogDescription>
              </DialogHeader>
              {folderNumberTarget && (
                <div className="space-y-3">
                  <div
                    className="rounded-ui-md border border-warning/40 bg-warning/10 px-3 py-3 text-ui-sm"
                    role="status"
                  >
                    <p className="font-medium">
                      编号 {String(parsedFolderNumber).padStart(2, "0")} 当前由“
                      {folderNumberConflict?.display_name}”使用
                    </p>
                    <p className="mt-1 text-ui-xs text-muted-foreground">
                      “{folderNumberTarget.display_name}”将从第{" "}
                      {currentFolderNumber} 位移动到第 {parsedFolderNumber}{" "}
                      位，共{" "}
                      {Math.abs(currentFolderNumber - parsedFolderNumber) + 1}{" "}
                      个同级文件夹的编号可能变化。
                    </p>
                  </div>
                  <p className="text-ui-sm text-muted-foreground">
                    文件夹内容、资料归属和稳定分类标识不会改变。
                  </p>
                  {folderActionError && (
                    <p className="text-ui-sm text-destructive" role="alert">
                      {folderActionError}
                    </p>
                  )}
                </div>
              )}
              <DialogFooter>
                <Button
                  variant="outline"
                  onClick={() => {
                    setFolderNumberTarget(null);
                    setFolderNumberConfirming(false);
                  }}
                  disabled={
                    busyAction === `folder:${folderNumberTarget?.id}:number`
                  }
                >
                  取消
                </Button>
                <Button
                  onClick={() => void saveFolderNumber()}
                  disabled={
                    busyAction === `folder:${folderNumberTarget?.id}:number`
                  }
                >
                  {busyAction === `folder:${folderNumberTarget?.id}:number`
                    ? "调整中…"
                    : "继续调整"}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(folderRenameTarget)}
        onOpenChange={(open) => {
          if (!open && !busyAction?.startsWith("folder:")) {
            setFolderRenameTarget(null);
            setFolderActionError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>重命名文件夹</DialogTitle>
            <DialogDescription>
              只修改文件夹名称；显示编号、资料归属和发布状态保持不变。
            </DialogDescription>
          </DialogHeader>
          {folderRenameTarget && (
            <div className="space-y-4">
              <div className="rounded-ui-md border border-border bg-surface-muted/40 px-3 py-2 text-ui-sm">
                <p className="text-ui-xs text-muted-foreground">当前路径</p>
                <p className="mt-1 break-words font-medium">
                  {folderRenameTarget.full_path}
                </p>
              </div>
              <label className="block space-y-1.5 text-ui-sm font-medium">
                <span>文件夹名称</span>
                <Input
                  value={folderRenameName}
                  maxLength={100}
                  onChange={(event) => {
                    setFolderRenameName(event.target.value);
                    setFolderActionError(null);
                  }}
                  aria-label="文件夹名称"
                  autoFocus
                />
                <span className="block text-right text-ui-xs font-normal text-muted-foreground">
                  {folderRenameName.trim().length}/100
                </span>
              </label>
              {folderRenameConflict && (
                <p
                  className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui-sm text-destructive"
                  role="alert"
                >
                  当前目录已有同名文件夹“{folderRenameConflict.display_name}
                  ”，请使用其他名称。
                </p>
              )}
              {folderActionError && (
                <p className="text-ui-sm text-destructive" role="alert">
                  {folderActionError}
                </p>
              )}
            </div>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setFolderRenameTarget(null)}
              disabled={
                busyAction === `folder:${folderRenameTarget?.id}:rename`
              }
            >
              取消
            </Button>
            <Button
              onClick={() => void saveFolderRename()}
              disabled={
                !folderRenameName.trim() ||
                Boolean(folderRenameConflict) ||
                normalizeFolderName(folderRenameName) ===
                  normalizeFolderName(folderRenameTarget?.display_name || "") ||
                busyAction === `folder:${folderRenameTarget?.id}:rename`
              }
            >
              {busyAction === `folder:${folderRenameTarget?.id}:rename`
                ? "保存中…"
                : "保存名称"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(folderMoveTarget)}
        onOpenChange={(open) => {
          if (!open && !busyAction?.startsWith("folder:")) {
            setFolderMoveTarget(null);
            setFolderMoveParentId("");
            setFolderActionError(null);
          }
        }}
      >
        <DialogContent className="max-h-[calc(100vh-2rem)] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>移动文件夹位置</DialogTitle>
            <DialogDescription>
              移动会更新完整路径，但不会改变资料归属、发布状态或稳定分类标识。
            </DialogDescription>
          </DialogHeader>
          {folderMoveTarget && (
            <div className="space-y-4">
              <div className="rounded-ui-md border border-border bg-surface-muted/40 px-3 py-2 text-ui-sm">
                <p className="text-ui-xs text-muted-foreground">当前路径</p>
                <p className="mt-1 break-words font-medium">
                  {folderMoveTarget.full_path}
                </p>
              </div>
              <CategoryDestinationPicker
                categories={categories}
                value={folderMoveParentId}
                onChange={(value) => {
                  setFolderMoveParentId(value);
                  setFolderActionError(null);
                }}
                currentCategoryId={folderMoveTarget.parent_id}
                label="目标位置"
                rootOption={{
                  value: ROOT_FOLDER_VALUE,
                  label: "根目录 /",
                  disabledReason: folderMoveConstraints.rootReason,
                }}
                disabledCategoryReasons={folderMoveConstraints.reasons}
                disabled={busyAction === `folder:${folderMoveTarget.id}:move`}
                onCreateFolder={
                  can("category.manage") ? createDestinationFolder : undefined
                }
              />
              {folderMoveParentId && (
                <p className="break-words text-ui-xs text-muted-foreground">
                  目标目录：
                  {folderMoveParentId === ROOT_FOLDER_VALUE
                    ? "/"
                    : categories.find(
                        (category) => category.id === folderMoveParentId,
                      )?.full_path}
                  ；移动后自动排在末尾并重新编号。
                </p>
              )}
              {folderActionError && (
                <p className="text-ui-sm text-destructive" role="alert">
                  {folderActionError}
                </p>
              )}
            </div>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setFolderMoveTarget(null)}
              disabled={busyAction === `folder:${folderMoveTarget?.id}:move`}
            >
              取消
            </Button>
            <Button
              onClick={() => void saveFolderMove()}
              disabled={
                !folderMoveParentId ||
                Boolean(
                  folderMoveParentId === ROOT_FOLDER_VALUE
                    ? folderMoveConstraints.rootReason
                    : folderMoveConstraints.reasons[folderMoveParentId],
                ) ||
                busyAction === `folder:${folderMoveTarget?.id}:move`
              }
            >
              <FolderInput className="size-4" />
              {busyAction === `folder:${folderMoveTarget?.id}:move`
                ? "移动中…"
                : "确认移动"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(bulkAction && bulkAction !== "archive")}
        onOpenChange={(open) => {
          if (!open && busyAction !== "bulk") {
            setBulkAction(null);
            setBulkFailures([]);
            setBulkMoveFolderId("");
            setBulkNote("");
            setBulkWorkbenchTargets([]);
            setBulkWorkbenchResults({});
          }
        }}
      >
        <DialogContent
          className={
            bulkAction === "move" ||
            bulkAction === "publish" ||
            bulkAction === "submit" ||
            bulkAction === "approve" ||
            bulkAction === "reject"
              ? "max-h-[calc(100vh-2rem)] max-w-3xl overflow-y-auto"
              : undefined
          }
        >
          <DialogHeader>
            <DialogTitle>
              {bulkAction === "move"
                ? bulkMoveLabel
                : bulkAction === "publish"
                  ? "批量发布资料"
                  : bulkAction === "submit"
                    ? "批量提交审核"
                    : "批量审核资料"}
            </DialogTitle>
            <DialogDescription>
              {bulkAction === "publish"
                ? `已选择 ${selectedItems.length} 份资料，请确认本次实际发布范围。`
                : bulkAction === "submit" ||
                    bulkAction === "approve" ||
                    bulkAction === "reject"
                  ? `本次工作台包含 ${bulkWorkbenchTargets.length} 份资料。单独处理成功的资料不会再被一键操作重复处理。`
                  : `已选择 ${selectedItems.length} 份资料。系统会逐项执行，并保留不符合状态或权限要求的失败原因。`}
            </DialogDescription>
          </DialogHeader>
          {bulkAction === "move" && (
            <CategoryDestinationPicker
              categories={categories}
              value={bulkMoveFolderId}
              onChange={setBulkMoveFolderId}
              label="目标目录"
              onCreateFolder={
                can("category.manage") ? createDestinationFolder : undefined
              }
            />
          )}
          {bulkAction === "publish" && (
            <>
              <div className="flex flex-wrap gap-2 text-ui-xs" role="status">
                <Badge variant="success">
                  将发布 {publishableSelectedItems.length}
                </Badge>
                {skippedPublishSelectedItems.length > 0 && (
                  <Badge variant="outline">
                    已跳过 {skippedPublishSelectedItems.length}
                  </Badge>
                )}
              </div>
              <div className="space-y-2">
                <p className="text-ui-sm font-medium">本次将批量发布</p>
                <div
                  className="max-h-64 divide-y divide-border overflow-y-auto border-y border-border"
                  aria-label="批量发布文件列表"
                >
                  {publishableSelectedItems.map((item) => (
                    <div
                      key={item.version_id}
                      className="grid gap-2 py-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
                    >
                      <div className="min-w-0">
                        <p className="break-words text-ui-sm font-medium">
                          {item.title}
                        </p>
                        <p className="mt-1 break-all text-ui-xs text-muted-foreground">
                          {item.original_filename} ·{" "}
                          {item.category_path || item.category_label}
                        </p>
                      </div>
                      <Badge variant="outline">
                        {statusLabel[item.lifecycle_status] ||
                          item.lifecycle_status}
                      </Badge>
                    </div>
                  ))}
                </div>
              </div>
              {skippedPublishSelectedItems.length > 0 && (
                <div className="space-y-2">
                  <p className="text-ui-sm font-medium text-muted-foreground">
                    不会重复发布
                  </p>
                  <div
                    className="max-h-40 divide-y divide-border overflow-y-auto border-y border-border"
                    aria-label="批量发布跳过列表"
                  >
                    {skippedPublishSelectedItems.map((item) => (
                      <div
                        key={item.version_id}
                        className="grid gap-2 py-2.5 text-ui-sm sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
                      >
                        <span className="break-words">{item.title}</span>
                        <span className="text-ui-xs text-muted-foreground">
                          {item.lifecycle_status === "published"
                            ? "已发布，无需重复操作"
                            : `${statusLabel[item.lifecycle_status] || item.lifecycle_status}，当前不可发布`}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
          {(bulkAction === "submit" ||
            bulkAction === "approve" ||
            bulkAction === "reject") && (
            <>
              <div className="flex flex-wrap gap-2 text-ui-xs" role="status">
                {bulkPendingSubmitIds.length > 0 && (
                  <Badge>待提交 {bulkPendingSubmitIds.length}</Badge>
                )}
                {bulkPendingReviewIds.length > 0 && (
                  <Badge variant="warning">
                    待审核 {bulkPendingReviewIds.length}
                  </Badge>
                )}
                {bulkSubmittedCount > 0 && (
                  <Badge>已提交 {bulkSubmittedCount}</Badge>
                )}
                {Object.values(bulkWorkbenchResults).filter(
                  (value) => value === "approved",
                ).length > 0 && (
                  <Badge variant="success">
                    已通过{" "}
                    {
                      Object.values(bulkWorkbenchResults).filter(
                        (value) => value === "approved",
                      ).length
                    }
                  </Badge>
                )}
                {Object.values(bulkWorkbenchResults).filter(
                  (value) => value === "rejected",
                ).length > 0 && (
                  <Badge variant="warning">
                    已退回{" "}
                    {
                      Object.values(bulkWorkbenchResults).filter(
                        (value) => value === "rejected",
                      ).length
                    }
                  </Badge>
                )}
              </div>
              <div
                className="max-h-80 divide-y divide-border overflow-y-auto border-y border-border"
                aria-label="批量审核文件列表"
              >
                {bulkWorkbenchTargets.map((item) => {
                  const result = bulkWorkbenchResults[item.version_id];
                  const failure = bulkWorkbenchFailureById.get(item.version_id);
                  const canSubmitRow =
                    ["draft", "rejected"].includes(item.lifecycle_status) &&
                    !result;
                  const canReviewRow =
                    (item.lifecycle_status === "awaiting_review" ||
                      result === "submitted") &&
                    result !== "approved" &&
                    result !== "rejected";
                  const rowBusy = bulkItemBusy === item.version_id;
                  return (
                    <div
                      key={item.version_id}
                      className="grid gap-3 py-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
                    >
                      <div className="min-w-0">
                        <p className="break-words text-ui-sm font-medium">
                          {item.title}
                        </p>
                        <p className="mt-1 break-all text-ui-xs text-muted-foreground">
                          {item.original_filename} ·{" "}
                          {item.category_path || item.category_label}
                        </p>
                        {failure && (
                          <p
                            className="mt-1 break-words text-ui-xs text-destructive"
                            role="alert"
                          >
                            {failure}
                          </p>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center justify-end gap-2">
                        {result === "approved" ? (
                          <Badge variant="success">已通过</Badge>
                        ) : result === "rejected" ? (
                          <Badge variant="warning">已退回</Badge>
                        ) : result === "submitted" ? (
                          <Badge>已提交</Badge>
                        ) : (
                          <Badge variant="outline">
                            {statusLabel[item.lifecycle_status] ||
                              item.lifecycle_status}
                          </Badge>
                        )}
                        {canSubmitRow && (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={
                              Boolean(busyAction) || Boolean(bulkItemBusy)
                            }
                            onClick={() =>
                              void executeBulkWorkbench(
                                "submit",
                                [item.version_id],
                                true,
                              )
                            }
                          >
                            <Send className="size-4" />
                            {rowBusy ? "提交中…" : "提交审核"}
                          </Button>
                        )}
                        {canReviewRow && false && (
                          <>
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={
                                Boolean(busyAction) || Boolean(bulkItemBusy)
                              }
                              onClick={() =>
                                void executeBulkWorkbench(
                                  "approve",
                                  [item.version_id],
                                  true,
                                )
                              }
                            >
                              <Check className="size-4" />
                              {rowBusy ? "处理中…" : "通过"}
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={
                                Boolean(busyAction) ||
                                Boolean(bulkItemBusy) ||
                                !bulkNote.trim()
                              }
                              title={
                                !bulkNote.trim()
                                  ? "请先填写退回原因"
                                  : undefined
                              }
                              onClick={() =>
                                void executeBulkWorkbench(
                                  "reject",
                                  [item.version_id],
                                  true,
                                )
                              }
                            >
                              <X className="size-4" />
                              退回
                            </Button>
                          </>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
          {(bulkAction === "submit" ||
            bulkAction === "approve" ||
            bulkAction === "reject") &&
            false && (
              <label className="block space-y-1.5 text-ui-sm font-medium">
                <span>退回原因（退回时必填）</span>
                <textarea
                  aria-label="批量退回原因"
                  value={bulkNote}
                  onChange={(event) => setBulkNote(event.target.value)}
                  maxLength={2000}
                  className="min-h-28 w-full resize-y rounded-ui-md border border-input bg-background px-3 py-2 text-ui-sm"
                  placeholder="请说明需要修改的内容"
                />
                <span className="block text-right text-ui-xs font-normal text-muted-foreground">
                  {bulkNote.length}/2000
                </span>
              </label>
            )}
          {bulkFailures.length > 0 &&
            !["submit", "approve", "reject"].includes(bulkAction || "") && (
              <div
                className="space-y-2 text-ui-sm text-destructive"
                role="alert"
              >
                <p>上次操作有 {bulkFailures.length} 份失败：</p>
                <ul className="max-h-48 space-y-1 overflow-y-auto border-y border-destructive/30 py-2">
                  {bulkFailures.map((entry) => (
                    <li key={entry.version_id} className="break-words">
                      <span className="font-medium">{entry.title}</span>
                      {entry.message ? `：${entry.message}` : "：请刷新后重试"}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setBulkAction(null)}
              disabled={busyAction === "bulk"}
            >
              取消
            </Button>
            {["submit", "approve", "reject"].includes(bulkAction || "") ? (
              <>
                {bulkPendingSubmitIds.length > 0 && (
                  <Button
                    variant="outline"
                    disabled={Boolean(busyAction) || Boolean(bulkItemBusy)}
                    onClick={() =>
                      void executeBulkWorkbench("submit", bulkPendingSubmitIds)
                    }
                  >
                    <Send className="size-4" />
                    {busyAction === "bulk"
                      ? "提交中…"
                      : `一键提交（${bulkPendingSubmitIds.length}）`}
                  </Button>
                )}
                {false && bulkPendingReviewIds.length > 0 && (
                  <>
                    <Button
                      variant="outline"
                      disabled={
                        Boolean(busyAction) ||
                        Boolean(bulkItemBusy) ||
                        !bulkNote.trim()
                      }
                      onClick={() =>
                        void executeBulkWorkbench(
                          "reject",
                          bulkPendingReviewIds,
                        )
                      }
                    >
                      <X className="size-4" />
                      一键退回（{bulkPendingReviewIds.length}）
                    </Button>
                    <Button
                      disabled={Boolean(busyAction) || Boolean(bulkItemBusy)}
                      onClick={() =>
                        void executeBulkWorkbench(
                          "approve",
                          bulkPendingReviewIds,
                        )
                      }
                    >
                      <Check className="size-4" />
                      一键通过（{bulkPendingReviewIds.length}）
                    </Button>
                  </>
                )}
              </>
            ) : (
              <Button
                onClick={() => void executeBulk()}
                disabled={
                  busyAction === "bulk" ||
                  selectedItems.length === 0 ||
                  (bulkAction === "move" && !bulkMoveFolderId) ||
                  (bulkAction === "publish" &&
                    publishableSelectedItems.length === 0)
                }
              >
                {busyAction === "bulk"
                  ? "处理中…"
                  : bulkFailures.length
                    ? "重试失败项"
                    : bulkAction === "publish"
                      ? `确认发布（${publishableSelectedItems.length}）`
                      : "确认执行"}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={
          Boolean(detail) && !previewState.parentId && !previewState.versionId
        }
        onOpenChange={(open) => {
          if (!open && !previewState.parentId && !previewState.versionId)
            setDetail(null);
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{detail?.title || "资料详情"}</DialogTitle>
            <DialogDescription>
              {detail?.content_kind === "media_transcript"
                ? "查看已发布的视频转录稿；校对、发布和失败恢复在转录任务中完成。"
                : "核对文件、分类、来源、版本和最近审核记录。"}
            </DialogDescription>
          </DialogHeader>
          {detail && (
            <div className="space-y-4">
              <PublicationFailure item={detail} />
              <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-2 text-ui-sm [&_dt]:whitespace-nowrap">
                <dt className="text-muted-foreground">类型</dt>
                <dd>
                  {detail.content_kind === "media_transcript"
                    ? "视频转录稿"
                    : "文档"}
                </dd>
                <dt className="text-muted-foreground">文件名</dt>
                <dd className="break-all">{detail.original_filename}</dd>
                <dt className="text-muted-foreground">分类</dt>
                <dd className="break-words">
                  {detail.category_path || detail.category_label}
                </dd>
                <dt className="text-muted-foreground">状态</dt>
                <dd>
                  <Badge variant={statusVariant(detail.lifecycle_status)}>
                    {statusLabel[detail.lifecycle_status]}
                  </Badge>
                </dd>
                <dt className="text-muted-foreground">来源</dt>
                <dd>{sourceLabel[detail.source_origin] || "其他来源"}</dd>
                <dt className="text-muted-foreground">版本</dt>
                <dd>v{detail.version_number}</dd>
                {detail.content_kind === "media_transcript" && (
                  <>
                    <dt className="text-muted-foreground">视频时长</dt>
                    <dd>
                      {formatMediaDuration(detail.media_duration_ms) ||
                        "未记录"}
                    </dd>
                    <dt className="text-muted-foreground">视频大小</dt>
                    <dd>
                      {detail.media_file_size != null
                        ? formatUploadSize(detail.media_file_size)
                        : "未记录"}
                    </dd>
                    <dt className="text-muted-foreground">后续版本</dt>
                    <dd>
                      {detail.has_pending_revision ? (
                        <Badge variant="warning">有新转录稿待处理</Badge>
                      ) : (
                        "无待处理稿"
                      )}
                    </dd>
                  </>
                )}
                <dt className="text-muted-foreground">创建时间</dt>
                <dd>{formatAdminDate(detail.created_at)}</dd>
                <dt className="text-muted-foreground">最后更新时间</dt>
                <dd className="whitespace-nowrap">
                  {formatAdminDate(detail.updated_at)}
                </dd>
                <dt className="text-muted-foreground">发布尝试</dt>
                <dd>共 {detail.publication_attempt_count} 次</dd>
                {detail.latest_review_decision && (
                  <>
                    <dt className="text-muted-foreground">最近审核人</dt>
                    <dd>{detail.latest_reviewed_by_name || "未知"}</dd>
                    <dt className="text-muted-foreground">审核时间</dt>
                    <dd>
                      {detail.latest_reviewed_at
                        ? formatAdminDate(detail.latest_reviewed_at)
                        : "未知"}
                    </dd>
                    <dt className="text-muted-foreground">审核结果</dt>
                    <dd>
                      {detail.latest_review_decision === "approved"
                        ? "确认通过"
                        : "退回修改"}
                    </dd>
                    <dt className="text-muted-foreground">
                      {detail.latest_review_decision === "rejected"
                        ? "退回原因"
                        : "审核备注"}
                    </dt>
                    <dd className="break-words">
                      {detail.latest_review_note || "未填写"}
                    </dd>
                  </>
                )}
              </dl>
              {detail.content_kind === "media_transcript" ? (
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="outline"
                    onClick={() => {
                      setDetail(null);
                      openVideoPreview({
                        mediaId: detail.media_id!,
                        title: detail.title,
                        startSeconds: 0,
                        fromSource: false,
                      });
                    }}
                    disabled={!detail.media_id}
                  >
                    <Film className="size-4" />
                    播放视频与转录稿
                  </Button>
                  {state.status === "authed" && state.user.role === "admin" && (
                    <a
                      className={buttonVariants({ variant: "outline" })}
                      href={`/admin/content?view=transcription&media_id=${encodeURIComponent(detail.media_id || "")}&workbench=1`}
                    >
                      <ExternalLink className="size-4" />
                      进入转录任务
                    </a>
                  )}
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="outline"
                    onClick={() => {
                      const target = detail;
                      setDetail(null);
                      void openAudit(target);
                    }}
                  >
                    <History className="size-4" />
                    操作记录
                  </Button>
                  {detail.doc_type === "xmind" ? (
                    <Button
                      variant="outline"
                      onClick={() =>
                        openXMind(
                          detail.version_id,
                          detail.title,
                          "managed-content-detail",
                        )
                      }
                    >
                      <Eye className="size-4" />
                      预览文件
                    </Button>
                  ) : detail.preview_parent_id &&
                    ["pdf", "docx", "xlsx", "pptx"].includes(
                      detail.doc_type,
                    ) ? (
                    <Button
                      variant="outline"
                      onClick={() => {
                        openDocumentPreview(
                          detail.preview_parent_id!,
                          detail.title,
                          detail.doc_type,
                          1,
                          {},
                          "managed-content-detail",
                        );
                      }}
                    >
                      <Eye className="size-4" />
                      预览文件
                    </Button>
                  ) : detail.doc_type === "pdf" || can("item.download") ? (
                    <a
                      className={buttonVariants({ variant: "outline" })}
                      href={adminContentApi.fileUrl(detail.version_id)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <Eye className="size-4" />
                      打开文件
                    </a>
                  ) : (
                    <Button
                      variant="outline"
                      disabled
                      title="打开文件（需要下载权限）"
                    >
                      <Eye className="size-4" />
                      打开文件
                    </Button>
                  )}
                  {detail.doc_type === "pptx" &&
                    detail.preview_status === "missing" &&
                    detail.lifecycle_status === "published" &&
                    can("item.publish") && (
                      <Button
                        variant="outline"
                        onClick={() => void regeneratePreview(detail)}
                        disabled={Boolean(busyAction)}
                      >
                        <RotateCcw
                          className={
                            busyAction === `${detail.version_id}:preview`
                              ? "size-4 animate-spin"
                              : "size-4"
                          }
                        />
                        {busyAction === `${detail.version_id}:preview`
                          ? "生成中…"
                          : "重新生成预览"}
                      </Button>
                    )}
                  {can("item.publish") &&
                    ["draft", "approved", "publication_failed"].includes(
                      detail.lifecycle_status,
                    ) && (
                      <Button
                        onClick={() => openPublishDialog(detail)}
                        disabled={Boolean(busyAction)}
                      >
                        <Rocket className="size-4" />
                        {detail.lifecycle_status === "publication_failed"
                          ? "重新发布"
                          : "发布"}
                      </Button>
                    )}
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog
        open={transcriptionDialogOpen}
        onOpenChange={(open) => {
          if (!open && !transcriptionDialogBusy) {
            setTranscriptionTargets([]);
            setTranscriptionPreflight(null);
            setTranscriptionRequestKey(null);
            setTranscriptionScope("media");
            setTranscriptionDialogOpen(false);
          }
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              {transcriptionScope === "category"
                ? "批量转录当前目录及子目录"
                : transcriptionScope === "batch"
                  ? "转录最近上传批次"
                  : `开始转录${transcriptionTargets.length > 1 ? `（${transcriptionTargets.length} 个视频）` : ""}`}
            </DialogTitle>
            <DialogDescription>
              视频先保留在资料库；确认方案后才会创建转录任务。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <label className="block space-y-1.5 text-ui-sm font-medium">
              <span>转录方案</span>
              <Select
                aria-label="选择转录方案"
                value={videoSchemeId}
                onChange={(event) => {
                  setVideoSchemeId(event.target.value);
                  setTranscriptionPreflight(null);
                }}
              >
                <option value="">请选择可用方案</option>
                {transcriptionSchemes.map((scheme) => (
                  <option
                    key={scheme.scheme_id}
                    value={scheme.scheme_id}
                    disabled={
                      !scheme.enabled ||
                      scheme.archived ||
                      scheme.availability !== "available"
                    }
                  >
                    {scheme.name}
                    {scheme.availability !== "available" ? "（不可用）" : ""}
                  </option>
                ))}
              </Select>
            </label>
            {transcriptionScope === "category" ? (
              <p className="rounded-ui-md border border-border bg-surface-muted/40 px-3 py-2 text-ui-sm">
                范围：{currentFolder?.full_path || "当前目录"}
                及全部子目录，服务端会自动汇总其中的待转录视频。
              </p>
            ) : transcriptionScope === "batch" ? (
              <p className="rounded-ui-md border border-border bg-surface-muted/40 px-3 py-2 text-ui-sm">
                范围：最近一次上传批次，服务端会自动筛选其中的视频资料。
              </p>
            ) : (
              <ul className="max-h-48 space-y-1 overflow-y-auto rounded-ui-md border border-border px-3 py-2 text-ui-sm">
                {transcriptionTargets.map((item) => (
                  <li
                    key={item.item_id}
                    className="flex flex-wrap justify-between gap-2"
                  >
                    <span className="min-w-0 break-all">{item.title}</span>
                    <span className="text-ui-xs text-muted-foreground">
                      {item.category_path}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {transcriptionPreflight && (
              <div className="space-y-2">
                <p className="text-ui-sm">
                  可启动 <strong>{transcriptionPreflight.ready_count}</strong>{" "}
                  个，已跳过/不可用{" "}
                  <strong>{transcriptionPreflight.blocked_count}</strong> 个。
                </p>
                <ul className="max-h-48 space-y-1 overflow-y-auto border-y border-border py-2 text-ui-xs">
                  {transcriptionPreflight.items.map((item) => (
                    <li
                      key={item.media_id}
                      className="flex flex-wrap justify-between gap-2"
                    >
                      <span className="break-all">{item.title}</span>
                      <span
                        className={
                          item.status === "ready"
                            ? "text-success"
                            : "text-muted-foreground"
                        }
                      >
                        {item.status === "ready"
                          ? "可启动"
                          : item.reason || "已跳过"}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={transcriptionDialogBusy}
              onClick={() => {
                setTranscriptionTargets([]);
                setTranscriptionPreflight(null);
                setTranscriptionRequestKey(null);
                setTranscriptionScope("media");
                setTranscriptionDialogOpen(false);
              }}
            >
              取消
            </Button>
            {!transcriptionPreflight ? (
              <Button
                disabled={
                  !videoSchemeId ||
                  transcriptionDialogBusy ||
                  (transcriptionScope === "category" && !currentFolderId)
                }
                onClick={() => void runTranscriptionPreflight()}
              >
                {transcriptionDialogBusy ? "检查中…" : "检查可启动视频"}
              </Button>
            ) : (
              <Button
                disabled={
                  transcriptionDialogBusy ||
                  transcriptionPreflight.ready_count === 0
                }
                onClick={() => void executeTranscriptionStart()}
              >
                {transcriptionDialogBusy
                  ? "启动中…"
                  : `启动转录（${transcriptionPreflight.ready_count}）`}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {auditDialog}
      <CategoryDeleteDialog
        category={folderDeleteTarget}
        canForceDelete={can("category.force_delete")}
        onClose={() => setFolderDeleteTarget(null)}
        onDeleted={categoryDeleted}
      />

      <Dialog
        open={
          Boolean(reviewTarget) &&
          !previewState.parentId &&
          !previewState.versionId
        }
        onOpenChange={(open) => {
          if (
            !open &&
            !previewState.parentId &&
            !previewState.versionId &&
            busyAction !== "review"
          ) {
            setReviewTarget(null);
            setReviewError(null);
          }
        }}
      >
        <DialogContent className="max-h-[calc(100vh-2rem)] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>审核资料</DialogTitle>
            <DialogDescription>
              确认资料信息并记录本次审核结果。
            </DialogDescription>
          </DialogHeader>
          {reviewTarget && (
            <div className="space-y-4">
              <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-2 text-ui-sm [&_dt]:whitespace-nowrap">
                <dt className="text-muted-foreground">资料</dt>
                <dd className="break-words font-medium">
                  {reviewTarget.title}
                </dd>
                <dt className="text-muted-foreground">目录</dt>
                <dd className="break-words">
                  {reviewTarget.category_path || reviewTarget.category_label}
                </dd>
                <dt className="text-muted-foreground">文件</dt>
                <dd className="break-all">{reviewTarget.original_filename}</dd>
                <dt className="text-muted-foreground">版本</dt>
                <dd>v{reviewTarget.version_number}</dd>
                <dt className="text-muted-foreground">来源</dt>
                <dd>{sourceLabel[reviewTarget.source_origin] || "其他来源"}</dd>
              </dl>
              <div>
                {reviewTarget.doc_type === "xmind" ? (
                  <Button
                    variant="outline"
                    onClick={() =>
                      openXMind(
                        reviewTarget.version_id,
                        reviewTarget.title,
                        "managed-content-review",
                      )
                    }
                  >
                    <Eye className="size-4" />
                    预览文件
                  </Button>
                ) : reviewTarget.preview_parent_id &&
                  ["pdf", "docx", "xlsx", "pptx"].includes(
                    reviewTarget.doc_type,
                  ) ? (
                  <Button
                    variant="outline"
                    onClick={() =>
                      openDocumentPreview(
                        reviewTarget.preview_parent_id!,
                        reviewTarget.title,
                        reviewTarget.doc_type,
                        1,
                        {},
                        "managed-content-review",
                      )
                    }
                  >
                    <Eye className="size-4" />
                    预览文件
                  </Button>
                ) : reviewTarget.doc_type === "pdf" || can("item.download") ? (
                  <a
                    className={buttonVariants({ variant: "outline" })}
                    href={adminContentApi.fileUrl(reviewTarget.version_id)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <Eye className="size-4" />
                    打开文件
                  </a>
                ) : (
                  <Button
                    variant="outline"
                    disabled
                    title="打开文件（需要下载权限）"
                  >
                    <Eye className="size-4" />
                    打开文件
                  </Button>
                )}
              </div>
              <label className="block space-y-1.5 text-ui-sm font-medium">
                <span>
                  {reviewDecision === "approve"
                    ? "审核备注（可选）"
                    : "退回原因"}
                </span>
                <textarea
                  aria-label={
                    reviewDecision === "approve"
                      ? "审核备注（可选）"
                      : "退回原因"
                  }
                  value={reviewNote}
                  onChange={(event) => {
                    setReviewNote(event.target.value);
                    setReviewError(null);
                  }}
                  maxLength={2000}
                  className="min-h-28 w-full resize-y rounded-ui-md border border-input bg-background px-3 py-2 text-ui-sm"
                  placeholder={
                    reviewDecision === "approve"
                      ? "可记录审核依据"
                      : "请说明需要修改的内容"
                  }
                  autoFocus
                />
                <span className="block text-right text-ui-xs font-normal text-muted-foreground">
                  {reviewNote.length}/2000
                </span>
              </label>
              {reviewError && (
                <p
                  className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui-sm text-destructive"
                  role="alert"
                >
                  {reviewError}
                </p>
              )}
            </div>
          )}
          <DialogFooter
            role="group"
            aria-label="审核操作"
            className="flex-col items-stretch sm:flex-row sm:items-center"
          >
            <Button
              className="w-full sm:w-auto"
              variant="outline"
              disabled={busyAction === "review"}
              onClick={() => setReviewTarget(null)}
            >
              取消
            </Button>
            <Button
              className="w-full sm:w-auto"
              type="button"
              variant="outline"
              disabled={busyAction === "review"}
              onClick={() => {
                setReviewDecision(
                  reviewDecision === "approve" ? "reject" : "approve",
                );
                setReviewError(null);
              }}
            >
              {reviewDecision === "approve" ? (
                <>
                  <X className="size-4" />
                  退回修改
                </>
              ) : (
                <>
                  <Check className="size-4" />
                  改为确认通过
                </>
              )}
            </Button>
            <Button
              className="w-full sm:w-auto"
              variant={reviewDecision === "reject" ? "destructive" : "default"}
              disabled={
                busyAction === "review" ||
                (reviewDecision === "reject" && !reviewNote.trim())
              }
              onClick={() => void submitReview()}
            >
              {busyAction === "review" ? (
                "提交中…"
              ) : reviewDecision === "approve" ? (
                <>
                  <Check className="size-4" />
                  确认通过
                </>
              ) : (
                <>
                  <X className="size-4" />
                  确认退回
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(publishTarget)}
        onOpenChange={(open) => {
          if (!open && busyAction !== "publish") {
            setPublishTarget(null);
            setPublishError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {publishTarget?.lifecycle_status === "publication_failed"
                ? "重新发布资料"
                : "发布资料"}
            </DialogTitle>
            <DialogDescription>
              发布后系统会创建索引任务，完成后资料才会进入知识库检索。
            </DialogDescription>
          </DialogHeader>
          {publishTarget && (
            <div className="space-y-3 text-ui-sm">
              <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-2">
                <dt className="text-muted-foreground">资料</dt>
                <dd className="break-words font-medium">
                  {publishTarget.title}
                </dd>
                <dt className="text-muted-foreground">目录</dt>
                <dd className="break-words">
                  {publishTarget.category_path || publishTarget.category_label}
                </dd>
                <dt className="text-muted-foreground">版本</dt>
                <dd>v{publishTarget.version_number}</dd>
              </dl>
              <PublicationFailure item={publishTarget} />
              {publishError && (
                <p
                  className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-destructive"
                  role="alert"
                >
                  {publishError}
                </p>
              )}
            </div>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              disabled={busyAction === "publish"}
              onClick={() => setPublishTarget(null)}
            >
              取消
            </Button>
            <Button
              disabled={busyAction === "publish"}
              onClick={() => void publishContent()}
            >
              {busyAction === "publish"
                ? "发布中…"
                : publishTarget?.lifecycle_status === "publication_failed"
                  ? "确认重新发布"
                  : "确认发布"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={requestFolderOpen} onOpenChange={setRequestFolderOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>申请新建文件夹</DialogTitle>
            <DialogDescription>
              申请将在“{currentFolder?.display_name || "当前目录"}
              ”下创建受控目录，由资料负责人审批。
            </DialogDescription>
          </DialogHeader>
          <label className="space-y-1.5 text-ui-sm font-medium">
            <span>文件夹名称</span>
            <Input
              value={requestFolderName}
              onChange={(event) => setRequestFolderName(event.target.value)}
              placeholder="例如：净高分析"
              autoFocus
            />
          </label>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRequestFolderOpen(false)}
              disabled={busyAction === "request-folder"}
            >
              取消
            </Button>
            <Button
              onClick={() => void requestFolder()}
              disabled={
                !requestFolderName.trim() || busyAction === "request-folder"
              }
            >
              {busyAction === "request-folder" ? "提交中…" : "提交申请"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={newFolderOpen} onOpenChange={setNewFolderOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建文件夹</DialogTitle>
            <DialogDescription>
              文件夹将建立在“{currentFolder?.display_name || "当前目录"}”下。
            </DialogDescription>
          </DialogHeader>
          <label className="space-y-1.5 text-ui-sm font-medium">
            <span>文件夹名称</span>
            <Input
              value={newFolderName}
              onChange={(event) => setNewFolderName(event.target.value)}
              placeholder="例如：净高分析"
              autoFocus
            />
          </label>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setNewFolderOpen(false)}
              disabled={busyAction === "new-folder"}
            >
              取消
            </Button>
            <Button
              onClick={() => void createFolder()}
              disabled={!newFolderName.trim() || busyAction === "new-folder"}
            >
              {busyAction === "new-folder" ? "创建中…" : "创建"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(moveTarget)}
        onOpenChange={(open) => {
          if (!open && !busyAction?.endsWith(":move")) {
            setMoveTarget(null);
            setMoveFolderId("");
            setMoveError(null);
          }
        }}
      >
        <DialogContent className="max-w-2xl max-h-[calc(100vh-2rem)] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              {moveTarget?.content_kind === "media_transcript"
                ? "调整归档目录"
                : moveTarget?.has_published_head
                  ? "调整分类"
                  : "移动资料"}
            </DialogTitle>
            <DialogDescription>
              {moveTarget?.content_kind === "media_transcript"
                ? `只调整“${moveTarget.title}”在资料库中的归档目录，不改变视频、转录发布状态或索引。`
                : moveTarget?.has_published_head
                  ? `调整“${moveTarget.title}”的正式分类。同步完成前资料仍保留在原目录并继续正常检索。`
                  : `将“${moveTarget?.title || "资料"}”从当前目录移动到另一个受控目录。`}
            </DialogDescription>
          </DialogHeader>
          {moveTarget && (
            <CategoryDestinationPicker
              categories={categories}
              value={moveFolderId}
              currentCategoryId={moveTarget.category_id}
              onChange={(categoryId) => {
                setMoveFolderId(categoryId);
                setMoveError(null);
              }}
              label="目标目录"
              onCreateFolder={
                can("category.manage") ? createDestinationFolder : undefined
              }
            />
          )}
          {moveError && (
            <p
              className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui-sm text-destructive"
              role="alert"
            >
              {moveError}
            </p>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setMoveTarget(null);
                setMoveFolderId("");
                setMoveError(null);
              }}
              disabled={Boolean(busyAction?.endsWith(":move"))}
            >
              取消
            </Button>
            <Button
              onClick={() => void moveContent()}
              disabled={
                !moveFolderId ||
                moveFolderId === moveTarget?.category_id ||
                Boolean(busyAction?.endsWith(":move"))
              }
            >
              {busyAction?.endsWith(":move")
                ? "处理中…"
                : moveTarget?.content_kind === "media_transcript"
                  ? "确认调整"
                  : moveTarget?.has_published_head
                    ? "提交分类调整"
                    : "确认移动"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(downloadTarget)}
        onOpenChange={(open) => {
          if (!open && !busyAction?.endsWith(":download"))
            setDownloadTarget(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>下载视频资料</DialogTitle>
            <DialogDescription>
              选择“{downloadTarget?.title}”需要下载的内容。
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-2" role="radiogroup" aria-label="下载内容">
            {(
              [
                {
                  value: "video",
                  title: "仅视频",
                  description: "下载原始 MP4 视频文件",
                  icon: <Film className="size-5" />,
                },
                {
                  value: "transcript",
                  title: "仅转录稿",
                  description: "下载当前正式发布的 Markdown",
                  icon: <FileText className="size-5" />,
                },
                {
                  value: "all",
                  title: "视频与转录稿",
                  description: "将 MP4 和 Markdown 打包为 ZIP",
                  icon: <Download className="size-5" />,
                },
              ] as const
            ).map((option) => (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={downloadPart === option.value}
                className={`flex min-h-16 items-center gap-3 rounded-ui-md border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${downloadPart === option.value ? "border-primary bg-primary/5" : "border-border hover:bg-surface-muted"}`}
                onClick={() => setDownloadPart(option.value)}
              >
                <span
                  className={
                    downloadPart === option.value
                      ? "text-primary"
                      : "text-muted-foreground"
                  }
                >
                  {option.icon}
                </span>
                <span className="min-w-0">
                  <span className="block text-ui-sm font-medium">
                    {option.title}
                  </span>
                  <span className="mt-0.5 block text-ui-xs text-muted-foreground">
                    {option.description}
                  </span>
                </span>
                {downloadPart === option.value && (
                  <Check className="ml-auto size-4 shrink-0 text-primary" />
                )}
              </button>
            ))}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={Boolean(busyAction?.endsWith(":download"))}
              onClick={() => setDownloadTarget(null)}
            >
              取消
            </Button>
            <Button
              disabled={Boolean(busyAction?.endsWith(":download"))}
              onClick={() => void downloadMedia()}
            >
              {busyAction?.endsWith(":download") ? "准备下载…" : "开始下载"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(mediaInfoTarget)}
        onOpenChange={(open) => {
          if (!open && busyAction !== "media-metadata") {
            setMediaInfoTarget(null);
            setMediaInfoError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑媒体信息</DialogTitle>
            <DialogDescription>
              保存后会创建待审核候选；当前正式名称会保留到候选审核、索引和发布全部成功。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <label className="block space-y-1.5 text-ui-sm font-medium">
              <span>视频标题</span>
              <Input
                value={mediaInfoTitle}
                onChange={(event) => setMediaInfoTitle(event.target.value)}
              />
            </label>
            <label className="block space-y-1.5 text-ui-sm font-medium">
              <span>源文件名</span>
              <Input
                value={mediaInfoFilename}
                onChange={(event) => setMediaInfoFilename(event.target.value)}
              />
              <span className="block text-ui-xs font-normal text-muted-foreground">
                保留 .mp4 扩展名，不能包含路径或文件系统非法字符。
              </span>
            </label>
            {mediaInfoError && (
              <p
                className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui-sm text-destructive"
                role="alert"
              >
                {mediaInfoError}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={busyAction === "media-metadata"}
              onClick={() => setMediaInfoTarget(null)}
            >
              取消
            </Button>
            <Button
              disabled={
                busyAction === "media-metadata" ||
                !mediaInfoTitle.trim() ||
                !mediaInfoFilename.trim()
              }
              onClick={() => void createMediaInfoRevision()}
            >
              {busyAction === "media-metadata" ? "保存中…" : "创建修订"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(renameTarget)}
        onOpenChange={(open) => {
          if (!open && busyAction !== "rename") {
            setRenameTarget(null);
            setRenameConflict(null);
            setRenameError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>重命名资料</DialogTitle>
            <DialogDescription>
              标题和源文件名会作为新草稿版本保存，之后需要重新确认并发布。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <label className="block space-y-1.5 text-ui-sm font-medium">
              <span>资料标题</span>
              <Input
                value={renameTitle}
                onChange={(event) => {
                  setRenameTitle(event.target.value);
                  setRenameConflict(null);
                }}
              />
            </label>
            <label className="block space-y-1.5 text-ui-sm font-medium">
              <span>源文件名</span>
              <Input
                value={renameFilename}
                onChange={(event) => {
                  setRenameFilename(event.target.value);
                  setRenameConflict(null);
                }}
              />
              <span className="block text-ui-xs font-normal text-muted-foreground">
                只能修改名称，不能改变文件扩展名。
              </span>
            </label>
            {renameConflict && (
              <div
                className="space-y-2 rounded-ui-md border border-warning/50 bg-warning/10 p-3 text-ui-sm"
                role="alert"
              >
                <p className="font-medium">当前目录存在同名资料，是否替换？</p>
                <p className="break-words">
                  {renameConflict.title}（{renameConflict.original_filename}）
                </p>
                <p className="text-muted-foreground">
                  替换会将上述资料移入回收站并立即停止检索；当前资料的新版本仍需重新确认和发布。
                </p>
              </div>
            )}
            {renameError && (
              <p className="text-ui-sm text-destructive" role="alert">
                {renameError}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={busyAction === "rename"}
              onClick={() => setRenameTarget(null)}
            >
              取消
            </Button>
            {renameConflict ? (
              <Button
                variant="destructive"
                disabled={busyAction === "rename"}
                onClick={() => void renameContent(true)}
              >
                {busyAction === "rename" ? "替换中…" : "确认替换并重命名"}
              </Button>
            ) : (
              <Button
                disabled={
                  busyAction === "rename" ||
                  !renameTitle.trim() ||
                  !renameFilename.trim()
                }
                onClick={() => void renameContent()}
              >
                {busyAction === "rename" ? "保存中…" : "保存为新版本"}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(updateTarget)}
        onOpenChange={(open) => {
          if (!open && busyAction !== "update") {
            setUpdateTarget(null);
            setUpdateConflict(null);
            setUpdateError(null);
            setUpdateFile(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>更新资料文件</DialogTitle>
            <DialogDescription>
              上传替换文件后会创建新草稿版本，旧发布版本会继续检索，直到新版本发布成功。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <label className="flex min-h-32 cursor-pointer flex-col items-center justify-center gap-2 rounded-ui-lg border border-dashed border-input bg-background px-4 py-5 text-center hover:bg-surface-muted focus-within:ring-2 focus-within:ring-ring">
              <FileUp className="size-6 text-primary" />
              <span className="text-ui-sm font-medium">
                {updateFile ? updateFile.name : "选择替换文件"}
              </span>
              <span className="text-ui-xs text-muted-foreground">
                支持 PDF、Markdown、Word、Excel、PPT 和 XMind
              </span>
              <input
                ref={updateFileInputRef}
                type="file"
                className="sr-only"
                aria-label="选择替换文件"
                accept=".pdf,.md,.docx,.xlsx,.pptx,.xmind"
                onChange={(event) => {
                  setUpdateFile(event.target.files?.[0] || null);
                  setUpdateConflict(null);
                }}
              />
            </label>
            {updateFile && (
              <div className="space-y-2">
                <p className="text-ui-sm font-medium">文件名处理</p>
                <div
                  className="grid grid-cols-2 gap-2"
                  role="group"
                  aria-label="文件名处理"
                >
                  <Button
                    type="button"
                    variant={
                      updateFilenameMode === "old" ? "default" : "outline"
                    }
                    aria-pressed={updateFilenameMode === "old"}
                    onClick={() => {
                      setUpdateFilenameMode("old");
                      setUpdateConflict(null);
                    }}
                  >
                    沿用原名称
                  </Button>
                  <Button
                    type="button"
                    variant={
                      updateFilenameMode === "new" ? "default" : "outline"
                    }
                    aria-pressed={updateFilenameMode === "new"}
                    onClick={() => {
                      setUpdateFilenameMode("new");
                      setUpdateConflict(null);
                    }}
                  >
                    使用新文件名
                  </Button>
                </div>
                <p className="break-all text-ui-xs text-muted-foreground">
                  {updateFilenameMode === "old"
                    ? `将使用原名称并匹配新格式：${filenameForOldMode(updateTarget?.original_filename || "", updateFile.name)}`
                    : `将使用：${updateFile.name}`}
                </p>
              </div>
            )}
            {updateConflict && (
              <div
                className="space-y-2 rounded-ui-md border border-warning/50 bg-warning/10 p-3 text-ui-sm"
                role="alert"
              >
                <p className="font-medium">当前目录存在同名资料，是否替换？</p>
                <p>
                  {updateConflict.title}（{updateConflict.original_filename}）
                </p>
                <p className="text-muted-foreground">
                  替换会将上述资料移入回收站并停止检索。
                </p>
              </div>
            )}
            {updateError && (
              <p className="text-ui-sm text-destructive" role="alert">
                {updateError}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={busyAction === "update"}
              onClick={() => setUpdateTarget(null)}
            >
              取消
            </Button>
            {updateConflict ? (
              <Button
                variant="destructive"
                disabled={busyAction === "update"}
                onClick={() => void updateContent(true)}
              >
                {busyAction === "update" ? "替换中…" : "确认替换并更新"}
              </Button>
            ) : (
              <Button
                disabled={!updateFile || busyAction === "update"}
                onClick={() => void updateContent()}
              >
                {busyAction === "update" ? "上传中…" : "确认更新"}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={uploadDialogOpen}
        onOpenChange={(open) => {
          if (!open) closeUploadDialog();
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>上传文件</DialogTitle>
            <DialogDescription>
              {isSystemAdmin
                ? "文档和视频都会先进入资料库；视频显示为待转录。"
                : "上传后可在资料流程中确认并发布文档。"}
            </DialogDescription>
          </DialogHeader>
          <div
            data-testid="managed-upload-dropzone"
            onDragEnter={(event) => {
              event.preventDefault();
              setDragActive(true);
            }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={(event) => {
              if (
                !event.currentTarget.contains(
                  event.relatedTarget as Node | null,
                )
              )
                setDragActive(false);
            }}
            onDrop={(event) => {
              event.preventDefault();
              setDragActive(false);
              void inspectDroppedUpload(event.dataTransfer, "select");
            }}
            className={`flex min-h-36 flex-col items-center justify-center gap-2 rounded-ui-lg border border-dashed px-4 py-6 text-center transition-colors duration-normal focus-within:ring-2 focus-within:ring-ring ${dragActive ? "border-primary bg-primary/5" : "border-input bg-background hover:bg-surface-muted"}`}
          >
            <Upload className="size-6 text-primary" />
            <span className="text-ui-sm font-medium">
              {folderScanning
                ? "正在读取文件夹…"
                : "拖动文件或文件夹到这里"}
            </span>
            <div className="flex flex-wrap items-center justify-center gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="max-sm:h-control-md"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading || uploadChecking || folderScanning}
              >
                <Upload className="size-4" />
                选择文件
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="max-sm:h-control-md"
                onClick={() => folderInputRef.current?.click()}
                disabled={uploading || uploadChecking || folderScanning}
              >
                <Folder className="size-4" />
                选择文件夹
              </Button>
            </div>
            <span className="text-ui-xs text-muted-foreground">
              支持 PDF、Markdown、Word、Excel、PPT、XMind
              {isSystemAdmin ? " 和 MP4" : ""}
            </span>
            <input
              ref={fileInputRef}
              aria-label="选择资料文件"
              type="file"
              multiple
              accept={
                isSystemAdmin
                  ? ".pdf,.md,.docx,.xlsx,.pptx,.xmind,.mp4"
                  : ".pdf,.md,.docx,.xlsx,.pptx,.xmind"
              }
              className="sr-only"
              disabled={uploading || uploadChecking || folderScanning}
              onChange={(event) =>
                acceptFiles(Array.from(event.target.files || []))
              }
            />
            <input
              ref={folderInputRef}
              aria-label="选择资料文件夹"
              type="file"
              multiple
              className="sr-only"
              disabled={uploading || uploadChecking || folderScanning}
              onChange={(event) =>
                selectFolder(Array.from(event.target.files || []))
              }
              {...({
                webkitdirectory: "",
                directory: "",
              } as React.InputHTMLAttributes<HTMLInputElement>)}
            />
          </div>
          {files.length > 0 && (
            <UploadSelectionList
              entries={files}
              defaultPublish={uploadDefaultPublish}
              overrides={uploadPublishOverrides}
              canPublish={can("item.publish")}
              onDefaultChange={setUploadDefaultPublish}
              onOverrideChange={(key, publish) =>
                setUploadPublishOverrides((current) => ({
                  ...current,
                  [key]: publish,
                }))
              }
              onRemove={(index) => {
                setFiles((current) =>
                  current.filter((_, itemIndex) => itemIndex !== index),
                );
                setUploadPublishOverrides({});
              }}
            />
          )}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={closeUploadDialog}
              disabled={uploading || uploadChecking || folderScanning}
            >
              取消
            </Button>
            <Button
              onClick={() => void confirmDialogUpload()}
              disabled={
                !files.length ||
                uploading ||
                uploadChecking ||
                folderScanning ||
                !currentFolderId
              }
            >
              {uploadChecking
                ? "检查冲突中…"
                : uploading
                  ? "上传中…"
                  : "确定上传"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={pendingUploadFiles.length > 0}
        onOpenChange={(open) => {
          if (!open && !uploading && !uploadChecking) {
            setPendingUploadFiles([]);
            setPendingUploadFolderId("");
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认上传</DialogTitle>
            <DialogDescription>
              将上传到“
              {categories.find(
                (category) => category.id === pendingUploadFolderId,
              )?.full_path ||
                currentFolder?.full_path ||
                "当前目录"}
              ”，确认后先检查同名资料。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 text-ui-sm">
            <p>共 {pendingUploadFiles.length} 个文件</p>
            <UploadSelectionList
              entries={pendingUploadFiles}
              defaultPublish={uploadDefaultPublish}
              overrides={uploadPublishOverrides}
              canPublish={can("item.publish")}
              onDefaultChange={setUploadDefaultPublish}
              onOverrideChange={(key, publish) =>
                setUploadPublishOverrides((current) => ({
                  ...current,
                  [key]: publish,
                }))
              }
              onRemove={(index) => {
                setPendingUploadFiles((current) =>
                  current.filter((_, itemIndex) => itemIndex !== index),
                );
                setUploadPublishOverrides({});
              }}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setPendingUploadFiles([]);
                setPendingUploadFolderId("");
              }}
              disabled={uploading || uploadChecking}
            >
              取消
            </Button>
            <Button
              onClick={() => void confirmFileDropUpload()}
              disabled={uploading || uploadChecking}
            >
              {uploadChecking
                ? "检查冲突中…"
                : uploading
                  ? "上传中…"
                  : "确定上传"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(pendingFolderUpload)}
        onOpenChange={(open) => {
          if (!open && !uploading && !uploadChecking) {
            setPendingFolderUpload(null);
            setPendingFolderUploadFolderId("");
          }
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>上传文件夹</DialogTitle>
            <DialogDescription>
              确认后将按相对路径检查并上传到“
              {categories.find(
                (category) => category.id === pendingFolderUploadFolderId,
              )?.full_path ||
                currentFolder?.full_path ||
                "当前目录"}
              ”。同名文件夹不会自动合并。
            </DialogDescription>
          </DialogHeader>
          {pendingFolderUpload && (
            <div className="space-y-4 text-ui-sm">
              <dl className="grid grid-cols-2 gap-2 rounded-ui-md border border-border bg-surface-muted/40 p-3 sm:grid-cols-4">
                <div className="col-span-2 sm:col-span-4">
                  <dt className="text-ui-xs text-muted-foreground">根文件夹</dt>
                  <dd className="mt-1 break-all font-medium">
                    {pendingFolderUpload.rootFolderNames.length > 1
                      ? `${pendingFolderUpload.rootFolderNames[0]} 等 ${pendingFolderUpload.rootFolderNames.length} 个根文件夹`
                      : pendingFolderUpload.rootFolderNames[0] || "所选文件夹"}
                  </dd>
                </div>
                <div>
                  <dt className="text-ui-xs text-muted-foreground">文件夹</dt>
                  <dd className="mt-1 font-medium tabular-nums">
                    {pendingFolderUpload.folderCount} 个
                  </dd>
                </div>
                <div>
                  <dt className="text-ui-xs text-muted-foreground">
                    可上传文件
                  </dt>
                  <dd className="mt-1 font-medium tabular-nums">
                    {pendingFolderUpload.fileCount} 个
                  </dd>
                </div>
                <div>
                  <dt className="text-ui-xs text-muted-foreground">已忽略</dt>
                  <dd className="mt-1 font-medium tabular-nums">
                    {pendingFolderUpload.ignoredEntries.length} 个
                  </dd>
                </div>
                <div>
                  <dt className="text-ui-xs text-muted-foreground">上传大小</dt>
                  <dd className="mt-1 font-medium tabular-nums">
                    {formatUploadSize(pendingFolderUpload.totalSize)}
                  </dd>
                </div>
              </dl>
              {pendingFolderUpload.ignoredEntries.length > 0 && (
                <div className="space-y-1">
                  <p className="text-ui-xs font-medium text-muted-foreground">
                    以下格式不受支持，将被忽略
                  </p>
                  <ul className="max-h-24 space-y-1 overflow-y-auto rounded-ui-md border border-warning/40 bg-warning/10 px-3 py-2 text-ui-xs">
                    {pendingFolderUpload.ignoredEntries.map((entry) => (
                      <li key={entry.relativePath} className="break-all">
                        {entry.relativePath}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="space-y-1">
                <p className="text-ui-xs font-medium text-muted-foreground">
                  将上传的文件
                </p>
                <UploadSelectionList
                  entries={pendingFolderUpload.entries}
                  defaultPublish={uploadDefaultPublish}
                  overrides={uploadPublishOverrides}
                  canPublish={can("item.publish")}
                  onDefaultChange={setUploadDefaultPublish}
                  onOverrideChange={(key, publish) =>
                    setUploadPublishOverrides((current) => ({
                      ...current,
                      [key]: publish,
                    }))
                  }
                  onRemove={(index) => {
                    setPendingFolderUpload((current) => {
                      if (!current) return null;
                      const entries = current.entries.filter(
                        (_, itemIndex) => itemIndex !== index,
                      );
                      return {
                        ...current,
                        entries,
                        fileCount: entries.length,
                        totalSize: entries.reduce(
                          (sum, entry) => sum + entry.file.size,
                          0,
                        ),
                      };
                    });
                    setUploadPublishOverrides({});
                  }}
                />
              </div>
            </div>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setPendingFolderUpload(null);
                setPendingFolderUploadFolderId("");
              }}
              disabled={uploading || uploadChecking}
            >
              取消
            </Button>
            <Button
              onClick={() => void confirmFolderUpload()}
              disabled={
                uploading || uploadChecking || !pendingFolderUpload?.fileCount
              }
            >
              {uploadChecking
                ? "检查冲突中…"
                : uploading
                  ? "上传中…"
                  : "开始上传"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        open={Boolean(uploadConflictReview)}
        onOpenChange={(open) => {
          if (!open && !uploadChecking && !uploading)
            resetUploadConflictReview();
        }}
      >
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>处理上传冲突</DialogTitle>
            <DialogDescription>
              上传前发现需要确认的目录或文件。未冲突的文件会正常上传，跳过的文件不会发送到服务器。
            </DialogDescription>
          </DialogHeader>
          {uploadConflictReview && (
            <div className="max-h-[60vh] space-y-5 overflow-y-auto pr-1 text-ui-sm">
              {uploadConflictReview.preflight.folder_conflicts.length > 0 && (
                <section
                  className="space-y-3"
                  aria-labelledby="upload-folder-conflicts-title"
                >
                  <div>
                    <h3
                      id="upload-folder-conflicts-title"
                      className="font-semibold"
                    >
                      文件夹名称冲突
                    </h3>
                    <p className="mt-1 text-ui-xs text-muted-foreground">
                      默认不合并到现有目录，请明确选择处理方式。
                    </p>
                  </div>
                  <ul className="space-y-2">
                    {uploadConflictReview.preflight.folder_conflicts.map(
                      (conflict) => (
                        <li
                          key={conflict.category_id}
                          className="rounded-ui-md border border-warning/50 bg-warning/10 p-3"
                        >
                          <p className="break-words font-medium">
                            “{conflict.relative_path}”已存在
                          </p>
                          <p className="mt-1 break-words text-ui-xs text-muted-foreground">
                            现有目录：{conflict.category_path}
                          </p>
                          <label className="mt-3 block space-y-1 text-ui-xs font-medium">
                            <span>重命名整个文件夹</span>
                            <Input
                              value={
                                folderConflictRenames[conflict.relative_path] ||
                                ""
                              }
                              disabled={
                                folderConflictMode !== "rename" ||
                                !conflict.can_rename
                              }
                              onChange={(event) =>
                                setFolderConflictRenames((current) => ({
                                  ...current,
                                  [conflict.relative_path]: event.target.value,
                                }))
                              }
                            />
                          </label>
                        </li>
                      ),
                    )}
                  </ul>
                  <div
                    className="flex flex-wrap gap-2"
                    role="group"
                    aria-label="文件夹冲突处理方式"
                  >
                    <Button
                      type="button"
                      variant={
                        folderConflictMode === "merge" ? "default" : "outline"
                      }
                      disabled={uploadChecking}
                      aria-pressed={folderConflictMode === "merge"}
                      onClick={() => void refreshConflictPreflight("merge")}
                    >
                      合并到现有目录
                    </Button>
                    <Button
                      type="button"
                      variant={
                        folderConflictMode === "rename" ? "default" : "outline"
                      }
                      disabled={
                        uploadChecking ||
                        uploadConflictReview.preflight.folder_conflicts.some(
                          (conflict) => !conflict.can_rename,
                        )
                      }
                      aria-pressed={folderConflictMode === "rename"}
                      onClick={() => setFolderConflictMode("rename")}
                    >
                      重命名文件夹
                    </Button>
                  </div>
                </section>
              )}
              {uploadConflictReview.preflight.entries.some(
                (entry) =>
                  entry.status === "conflict" &&
                  [
                    "content_filename_conflict",
                    "media_filename_conflict",
                  ].includes(entry.reason_code || ""),
              ) && (
                <section
                  className="space-y-3"
                  aria-labelledby="upload-file-conflicts-title"
                >
                  <div className="flex flex-wrap items-end justify-between gap-2">
                    <div>
                      <h3
                        id="upload-file-conflicts-title"
                        className="font-semibold"
                      >
                        文件名冲突
                      </h3>
                      <p className="mt-1 text-ui-xs text-muted-foreground">
                        每个文件可单独处理，也可以批量跳过。
                      </p>
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        setUploadConflictChoices((current) =>
                          Object.fromEntries(
                            Object.keys(current).map((key) => [
                              key,
                              {
                                strategy: "skip" as const,
                                filename: current[Number(key)]?.filename,
                              },
                            ]),
                          ),
                        )
                      }
                    >
                      全部跳过
                    </Button>
                  </div>
                  <ul className="space-y-3">
                    {uploadConflictReview.preflight.entries
                      .filter(
                        (entry) =>
                          entry.status === "conflict" &&
                          [
                            "content_filename_conflict",
                            "media_filename_conflict",
                          ].includes(entry.reason_code || ""),
                      )
                      .map((entry) => {
                        const choice = uploadConflictChoices[
                          entry.sequence
                        ] || {
                          strategy: "skip" as const,
                          filename: entry.suggested_filename || entry.filename,
                        };
                        return (
                          <li
                            key={entry.sequence}
                            className="space-y-3 rounded-ui-md border border-warning/50 bg-warning/10 p-3"
                          >
                            <div className="min-w-0">
                              <p className="break-all font-medium">
                                {entry.relative_path || entry.filename}
                              </p>
                              {entry.conflict && (
                                <p className="mt-1 break-words text-ui-xs text-muted-foreground">
                                  已有资料：{entry.conflict.title}（
                                  {entry.conflict.original_filename}）
                                </p>
                              )}
                              <p className="mt-1 text-ui-xs text-muted-foreground">
                                {entry.reason}
                              </p>
                            </div>
                            <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                              <label className="space-y-1 text-ui-xs font-medium">
                                <span>处理方式</span>
                                <Select
                                  value={choice.strategy}
                                  onChange={(event) =>
                                    setUploadConflictChoices((current) => ({
                                      ...current,
                                      [entry.sequence]: {
                                        ...current[entry.sequence],
                                        strategy: event.target
                                          .value as UploadConflictChoice["strategy"],
                                      },
                                    }))
                                  }
                                >
                                  <option value="skip">跳过此文件</option>
                                  <option value="rename">另存为新资料</option>
                                  {entry.conflict?.can_update && (
                                    <option value="update">
                                      作为已有资料的新版本
                                    </option>
                                  )}
                                </Select>
                              </label>
                              {choice.strategy === "rename" && (
                                <label className="space-y-1 text-ui-xs font-medium">
                                  <span>新文件名</span>
                                  <Input
                                    value={choice.filename || ""}
                                    onChange={(event) =>
                                      setUploadConflictChoices((current) => ({
                                        ...current,
                                        [entry.sequence]: {
                                          ...current[entry.sequence],
                                          strategy: "rename",
                                          filename: event.target.value,
                                        },
                                      }))
                                    }
                                  />
                                </label>
                              )}
                            </div>
                          </li>
                        );
                      })}
                  </ul>
                </section>
              )}
              {uploadConflictReview.preflight.entries.some(
                (entry) => entry.status === "blocked",
              ) && (
                <section className="space-y-2">
                  <h3 className="font-semibold">无法上传的文件</h3>
                  <ul className="space-y-1 rounded-ui-md border border-border px-3 py-2 text-ui-xs">
                    {uploadConflictReview.preflight.entries
                      .filter((entry) => entry.status === "blocked")
                      .map((entry) => (
                        <li
                          key={entry.sequence}
                          className="flex flex-wrap justify-between gap-2"
                        >
                          <span className="break-all">
                            {entry.relative_path || entry.filename}
                          </span>
                          <span className="text-muted-foreground">
                            {entry.reason}
                          </span>
                        </li>
                      ))}
                  </ul>
                </section>
              )}
              {uploadConflictError && (
                <p
                  className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui-sm text-destructive"
                  role="alert"
                >
                  {uploadConflictError}
                </p>
              )}
            </div>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={resetUploadConflictReview}
              disabled={uploadChecking || uploading}
            >
              取消
            </Button>
            <Button
              onClick={() => void confirmUploadConflictReview()}
              disabled={uploadChecking || uploading}
            >
              {uploadChecking
                ? "检查中…"
                : uploading
                  ? "上传中…"
                  : uploadConflictReview?.preflight.folder_conflicts.length
                    ? "继续检查文件冲突"
                    : "按选择上传"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={deleteTargets.length > 0}
        onOpenChange={(open) => {
          if (!open && busyAction !== "archive") {
            setDeleteTargets([]);
            setDeleteAcknowledged(false);
            setDeleteError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {deleteTargets.length > 1
                ? `将 ${deleteTargets.length} 份资料移入回收站？`
                : "将资料移入回收站？"}
            </DialogTitle>
            <DialogDescription>
              以下资料将立即停止进入知识库检索。文件、版本及审核发布历史会保留，可从回收站恢复。
            </DialogDescription>
          </DialogHeader>
          <ul className="max-h-48 space-y-2 overflow-y-auto rounded-ui-md border border-border p-3 text-ui-sm">
            {deleteTargets.map((item) => (
              <li key={item.item_id} className="min-w-0">
                <p className="break-words font-medium">{item.title}</p>
                <p className="break-all text-ui-xs text-muted-foreground">
                  {item.original_filename}
                </p>
              </li>
            ))}
          </ul>
          <label className="flex items-start gap-2 rounded-ui-md border border-destructive/30 bg-destructive/5 p-3 text-ui-sm">
            <Checkbox
              className="mt-0.5"
              checked={deleteAcknowledged}
              onChange={(event) => setDeleteAcknowledged(event.target.checked)}
            />
            <span>我已了解这些资料移入回收站后将不再进入检索。</span>
          </label>
          {deleteError && (
            <p
              className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui-sm text-destructive"
              role="alert"
            >
              {deleteError}
            </p>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              disabled={busyAction === "archive"}
              onClick={() => setDeleteTargets([])}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              disabled={busyAction === "archive" || !deleteAcknowledged}
              onClick={() => void deleteContent()}
            >
              {busyAction === "archive" ? "处理中…" : "确认移入回收站"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ManagedContentBulkOperationDialog
        action={recursiveBulkAction}
        selectedFolders={selectedFolderRows}
        selectedItems={selectedItems}
        categories={categories}
        onCreateFolder={
          can("category.manage") ? createDestinationFolder : undefined
        }
        onClose={() => setRecursiveBulkAction(null)}
        onCompleted={async () => {
          setSelected([]);
          setSelectedFolders([]);
          await load(true);
        }}
      />
    </section>
  );
}
