import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ArchiveRestore, ArrowDown, ArrowUp, ArrowUpDown, Check, ChevronDown, ChevronRight, Download, Eye, FileUp, Folder, FolderInput, FolderPlus, Info, Pencil, RefreshCw, Rocket, Search, Send, Trash2, Upload, X } from "lucide-react";
import { adminContentApi } from "../../api/admin/content";
import { Badge } from "../../components/ui/badge";
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
import { toast } from "../../components/ui/toast";
import { useAuth } from "../../context/AuthContext";
import { usePdfPreview } from "../../hooks/usePdfPreview";
import type { BulkManagedContentResult, ContentPermission, FolderRequest, ManagedCategory, ManagedContentItem, ManagedUploadResponse } from "../../types";
import { formatAdminDate } from "../../lib/admin-formatters";
import {
  collectDroppedUpload,
  folderSelectionFromFiles,
  type FolderUploadEntry,
  type FolderUploadSelection,
} from "../../lib/folder-upload";

const PAGE_SIZE = 25;
const PAGE_SIZE_OPTIONS = [25, 50, 100];
const BULK_LIMIT = 20;
type SortKey = "title" | "updatedAt" | "status" | "source";
type SortDirection = "asc" | "desc";

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
  const [pendingFolderUpload, setPendingFolderUpload] = useState<FolderUploadSelection | null>(null);
  const [pendingFolderUploadFolderId, setPendingFolderUploadFolderId] = useState("");
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
  const [deleteTargets, setDeleteTargets] = useState<ManagedContentItem[]>([]);
  const [deleteAcknowledged, setDeleteAcknowledged] = useState(false);
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
  const fileInputRef = useRef<HTMLInputElement>(null);
  const updateFileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const listDragDepthRef = useRef(0);

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
        adminContentApi.capabilities(), adminContentApi.categories(), adminContentApi.items({
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
      if (can("folder.review")) {
        setFolderRequests(await adminContentApi.folderRequests("pending"));
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "资料加载失败");
    } finally {
      setLoading(false); setRefreshing(false);
    }
  }, [categoryFilter, currentFolderId, page, pageSize, query, sourceFilter, statusFilter]);

  useEffect(() => { void load(); }, [load]);

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
    setUploading(true); setUploadResults([]);
    try {
      const result = uploadMode === "folder"
        ? await adminContentApi.upload(uploadFiles, targetFolderId, "folder")
        : await adminContentApi.upload(uploadFiles, targetFolderId);
      setUploadResults(result.entries);
      const accepted = result.entries.filter((entry) => entry.status === "accepted").length;
      const skipped = result.entries.length - accepted;
      toast.success(skipped ? `已接收 ${accepted} 个文件，跳过 ${skipped} 个` : `已接收 ${accepted} 个文件`);
      setFiles([]);
      if (fileInputRef.current) fileInputRef.current.value = "";
      if (folderInputRef.current) folderInputRef.current.value = "";
      await load(true);
      return true;
    } catch (uploadError) { toast.error(uploadError instanceof Error ? uploadError.message : "上传失败"); }
    finally { setUploading(false); }
    return false;
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
    return [...items].sort((left, right) => {
      const comparison = sort.key === "updatedAt"
        ? (left.updated_at || 0) - (right.updated_at || 0)
        : ({
          title: left.title.localeCompare(right.title, "zh-CN", { numeric: true, sensitivity: "base" }),
          status: (statusLabel[left.lifecycle_status] || left.lifecycle_status).localeCompare(statusLabel[right.lifecycle_status] || right.lifecycle_status, "zh-CN", { numeric: true, sensitivity: "base" }),
          source: (sourceLabel[left.source_origin] || left.source_origin).localeCompare(sourceLabel[right.source_origin] || right.source_origin, "zh-CN", { numeric: true, sensitivity: "base" }),
        })[sort.key];
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
      await adminContentApi.createCategory({
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
      await adminContentApi.move(moveTarget.item_id, moveFolderId, moveTarget.version_id);
      toast.success(`已移动“${moveTarget.title}”`); setMoveTarget(null); await load(true);
    } catch (moveError) { toast.error(moveError instanceof Error ? moveError.message : "移动资料失败"); }
    finally { setBusyAction(null); }
  };

  const moveItemTo = async (item: ManagedContentItem, targetFolderId: string) => {
    if (item.category_id === targetFolderId) return;
    setBusyAction(`${item.version_id}:move`);
    try {
      await adminContentApi.move(item.item_id, targetFolderId, item.version_id);
      toast.success(`已移动“${item.title}”`); setDraggedItem(null); await load(true);
    } catch (moveError) { toast.error(moveError instanceof Error ? moveError.message : "移动资料失败"); }
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
    try { await operation(); setDetail(null); toast.success(success); await load(true); }
    catch (actionError) { toast.error(actionError instanceof Error ? actionError.message : "操作失败"); }
    finally { setBusyAction(null); }
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
    setBusyAction("bulk-download");
    try {
      const result = await adminContentApi.bulkDownload(selectedItems.map((item) => item.version_id));
      triggerManagedDownload(result.blob, result.filename);
      toast.success(`已打包 ${selectedItems.length} 份资料并开始下载`);
    } catch (downloadError) {
      toast.error(downloadError instanceof Error ? downloadError.message : "批量下载失败");
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

  const canMoveItem = (item: ManagedContentItem) => (
    !item.has_published_head && (
      (can("item.move_draft") && ["draft", "rejected"].includes(item.lifecycle_status))
      || (can("item.move_review") && item.lifecycle_status === "awaiting_review")
    )
  );
  const canDeleteItem = (item: ManagedContentItem) => {
    const requiresPublish = item.has_published_head || !["draft", "rejected"].includes(item.lifecycle_status);
    return requiresPublish ? can("item.archive_published") : can("item.archive_draft");
  };

  const executeBulk = async () => {
    if (!bulkAction || selectedItems.length === 0) return;
    setBusyAction("bulk"); setBulkFailures([]);
    try {
      const ids = selectedItems.map((item) => item.version_id);
      const result = bulkAction === "move"
        ? await adminContentApi.bulkMove(
          selectedItems.map((item) => ({ item_id: item.item_id, expected_version_id: item.version_id })),
          bulkMoveFolderId,
        )
        : bulkAction === "publish"
        ? await adminContentApi.bulkPublish(ids)
        : await adminContentApi.bulkReview(ids, bulkAction === "approve");
      const titles = new Map(selectedItems.map((item) => [item.version_id, item.title]));
      const failures = result.results
        .filter((entry) => entry.status === "failed")
        .map((entry) => ({ ...entry, title: titles.get(entry.version_id) || "未知资料" }));
      setBulkFailures(failures);
      if (result.failed) toast.error(`成功 ${result.succeeded} 份，失败 ${result.failed} 份`);
      else toast.success(bulkAction === "publish" ? `已将 ${result.succeeded} 份资料加入发布队列` : bulkAction === "move" ? `已移动 ${result.succeeded} 份资料` : `已处理 ${result.succeeded} 份资料`);
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
  const hasMovableSelection = selectedItems.some(canMoveItem);
  const hasDeletableSelection = selectedItems.some(canDeleteItem);
  const hasDownloadableSelection = selectedItems.length > 1 && can("item.download");
  const bulkDisabled = Boolean(busyAction) || refreshing || !enabled;

  const renderActions = (item: ManagedContentItem) => {
    const disabled = Boolean(busyAction) || refreshing || !enabled;
    const previewable = Boolean(item.preview_parent_id && ["pdf", "docx", "xlsx", "pptx"].includes(item.doc_type));
    const movable = canMoveItem(item);
    const downloadable = can("item.download");
    const revisionAllowed = can("item.upload") && item.lifecycle_status !== "publishing";
    const deletable = canDeleteItem(item) && item.lifecycle_status !== "publishing";
    return <div className="ml-auto flex min-h-10 w-[19rem] items-center justify-end gap-1 sm:w-[17.25rem]">
      <IconButton label={`查看“${item.title}”的详细信息`} className="border border-border max-sm:size-10" disabled={disabled} onClick={() => setDetail(item)}><Info className="size-4" /></IconButton>
      <IconButton label={previewable ? `查看“${item.title}”` : `查看“${item.title}”（暂无可预览内容）`} className="border border-border max-sm:size-10" disabled={disabled || !previewable} onClick={() => openDocumentPreview(item.preview_parent_id!, item.title, item.doc_type, 1, {}, null)}><Eye className="size-4" /></IconButton>
      <IconButton label={movable ? `移动“${item.title}”` : `移动“${item.title}”（当前状态或权限不允许）`} className="border border-border max-sm:size-10" disabled={disabled || !movable} onClick={() => { setMoveTarget(item); setMoveFolderId(item.category_id); }}><FolderInput className="size-4" /></IconButton>
      <IconButton label={`下载“${item.title}”`} title={downloadable ? `下载“${item.title}”` : `下载“${item.title}”（需要下载权限）`} className="border border-border max-sm:size-10" disabled={disabled || !downloadable} onClick={() => void downloadContent(item)}><Download className="size-4" /></IconButton>
      <IconButton label={revisionAllowed ? `重命名“${item.title}”` : `重命名“${item.title}”（当前状态或权限不允许）`} className="border border-border max-sm:size-10" disabled={disabled || !revisionAllowed} onClick={() => openRenameDialog(item)}><Pencil className="size-4" /></IconButton>
      <IconButton label={revisionAllowed ? `更新“${item.title}”` : `更新“${item.title}”（当前状态或权限不允许）`} className="border border-border max-sm:size-10" disabled={disabled || !revisionAllowed} onClick={() => openUpdateDialog(item)}><FileUp className="size-4" /></IconButton>
      <IconButton label={deletable ? `删除“${item.title}”` : `删除“${item.title}”（当前状态或权限不允许）`} className="border border-destructive/30 text-destructive hover:bg-destructive/10 hover:text-destructive max-sm:size-10" disabled={disabled || !deletable} onClick={() => openDeleteDialog([item])}><Trash2 className="size-4" /></IconButton>
    </div>;
  };

  if (view === "trash") {
    const trashPageCount = Math.max(1, Math.ceil(trashTotal / PAGE_SIZE));
    return <section className="space-y-5" aria-labelledby="managed-content-title">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between"><div><p className="text-ui-xs font-medium text-primary">内容管理</p><h1 id="managed-content-title" className="mt-1 text-ui-2xl font-semibold tracking-tight">回收站</h1><p className="mt-1 text-ui-sm text-muted-foreground">{can("trash.restore") ? "查看和恢复已移出资料库的资料。" : "查看已移出资料库的资料。"}</p></div><Button size="sm" variant="outline" onClick={() => void loadTrash()} disabled={trashLoading}><RefreshCw className={trashLoading ? "size-4 animate-spin" : "size-4"} />刷新</Button></header>
      <div className="flex gap-2" role="tablist" aria-label="资料视图"><Button size="sm" variant="outline" role="tab" aria-selected="false" onClick={() => { setView("library"); setPage(0); }}>资料库</Button><Button size="sm" role="tab" aria-selected="true">回收站</Button></div>
      {error && <ErrorState title="回收站加载失败" description={error} action={<Button size="sm" variant="outline" onClick={() => void loadTrash()}>重新加载</Button>} />}
      <Card className="overflow-hidden shadow-surface"><div className="flex flex-col gap-3 border-b border-border px-4 py-4 sm:flex-row sm:items-end sm:justify-between sm:px-5"><label className="max-w-xl flex-1 space-y-1 text-ui-xs text-muted-foreground"><span>搜索回收站</span><span className="relative block"><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2" /><Input className="pl-9" value={queryInput} onChange={(event) => setQueryInput(event.target.value)} placeholder="搜索名称或文件名…" /></span></label><p className="text-ui-xs text-muted-foreground">共 {trashTotal} 份</p></div>
        {trashLoading ? <LoadingState className="min-h-48 border-0" label="正在加载回收站…" /> : trashItems.length === 0 ? <EmptyState className="rounded-none border-0" title="回收站为空" description="移至回收站的资料会显示在这里。" /> : <ul className="divide-y divide-border">{trashItems.map((item) => <li key={item.item_id} className="flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5"><div className="min-w-0"><p className="break-words font-medium">{item.title}</p><p className="mt-1 break-all text-ui-xs text-muted-foreground">{item.original_filename} · 原状态：{statusLabel[item.pre_archive_lifecycle_status || item.lifecycle_status] || "未知"}</p><p className="mt-1 text-ui-xs text-muted-foreground">{item.archived_by_name || "未知人员"} 于 {item.archived_at ? new Date(item.archived_at * 1000).toLocaleString("zh-CN") : "未知时间"} 移入回收站</p></div>{can("trash.restore") && <Button size="sm" variant="outline" disabled={Boolean(busyAction)} onClick={() => { setRestoreError(null); setRestoreTarget(item); }}><ArchiveRestore className="size-4" />恢复</Button>}</li>)}</ul>}
        <div className="flex items-center justify-between border-t border-border px-4 py-3 sm:px-5"><p className="text-ui-xs text-muted-foreground">第 {page + 1} / {trashPageCount} 页</p><div className="flex gap-2"><Button size="sm" variant="outline" disabled={page === 0 || trashLoading} onClick={() => setPage((value) => value - 1)}>上一页</Button><Button size="sm" variant="outline" disabled={page + 1 >= trashPageCount || trashLoading} onClick={() => setPage((value) => value + 1)}>下一页</Button></div></div>
      </Card>
      <Dialog open={Boolean(restoreTarget)} onOpenChange={(open) => { if (!open && !busyAction) { setRestoreTarget(null); setRestoreError(null); } }}><DialogContent><DialogHeader><DialogTitle>恢复资料</DialogTitle><DialogDescription>“{restoreTarget?.title}”将恢复到资料库。已发布或发布失败的资料会恢复为“已确认”，需要具备发布权限的人员重新发布后才会进入检索。</DialogDescription></DialogHeader>{restoreError && <p className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui-sm text-destructive" role="alert">{restoreError}</p>}<DialogFooter><Button variant="outline" disabled={Boolean(busyAction)} onClick={() => setRestoreTarget(null)}>取消</Button><Button disabled={Boolean(busyAction)} onClick={() => void restoreContent()}>{busyAction ? "恢复中…" : "确认恢复"}</Button></DialogFooter></DialogContent></Dialog>
    </section>;
  }

  return <section className="space-y-5" aria-labelledby="managed-content-title">
    <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div><p className="text-ui-xs font-medium text-primary">内容管理</p><h1 id="managed-content-title" className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">资料管理</h1><p className="mt-1 text-ui-sm text-muted-foreground">统一管理资料的上传、分类、确认和发布。</p></div>
    </header>

    {can("trash.view") && <div className="flex gap-2" role="tablist" aria-label="资料视图"><Button size="sm" role="tab" aria-selected="true">资料库</Button><Button size="sm" variant="outline" role="tab" aria-selected="false" onClick={() => { setView("trash"); setPage(0); setSelected([]); }}>回收站</Button></div>}

    <section className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="资料状态概览">
      {[["全部资料", Object.values(counts).reduce((sum, value) => sum + value, 0)], ["待确认", counts.awaiting_review || 0], ["已确认", counts.approved || 0], ["已发布", counts.published || 0]].map(([label, value]) => <Card key={label} className="overflow-hidden shadow-surface"><CardContent className="relative p-4 pt-4"><span className="absolute inset-x-0 top-0 h-1 bg-primary/80" aria-hidden="true" /><p className="text-ui-xs font-medium text-muted-foreground">{label}</p><p className="mt-2 text-ui-xl font-semibold tabular-nums text-foreground">{value}</p></CardContent></Card>)}
    </section>

    {!enabled && !loading && <div className="border border-warning/40 bg-warning/10 px-4 py-3 text-ui-sm" role="status">资料管理当前未启用，上传和流程操作暂不可用。</div>}
    {error && <ErrorState title="资料列表加载失败" description={error} action={<Button size="sm" variant="outline" onClick={() => void load()}>重新加载</Button>} />}

    {can("folder.review") && folderRequests.length > 0 && <Card className="overflow-hidden shadow-surface" aria-labelledby="folder-requests-title"><div className="border-b border-border px-4 py-3 sm:px-5"><h2 id="folder-requests-title" className="text-ui-base font-semibold">待处理目录申请</h2></div><ul className="divide-y divide-border">{folderRequests.map((request) => <li key={request.id} className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5"><div className="min-w-0"><p className="break-words text-ui-sm font-medium">{request.display_name}</p><p className="mt-0.5 text-ui-xs text-muted-foreground">上级目录：{request.parent_label} · 申请人：{request.requester_name || "未知"}</p></div><div className="flex gap-2"><Button size="sm" variant="outline" disabled={busyAction === `folder-request:${request.id}`} onClick={() => void reviewFolder(request, false)}><X className="size-4" />退回</Button><Button size="sm" disabled={busyAction === `folder-request:${request.id}`} onClick={() => void reviewFolder(request, true)}><Check className="size-4" />批准</Button></div></li>)}</ul></Card>}
    <Card className="overflow-hidden shadow-surface [&_table]:!min-w-[56rem]" aria-labelledby="managed-list-title">
      <div className="flex flex-col gap-3 border-b border-border px-4 py-4 lg:flex-row lg:items-end lg:justify-between sm:px-5">
        <div className="min-w-0"><h2 id="managed-list-title" className="text-ui-base font-semibold">资料列表</h2><p className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-ui-xs text-muted-foreground"><span>当前目录：{currentFolder?.full_path || "请选择目录"} · 共 {total} 份</span><span role="status" aria-live="polite">· {selected.length > 0 ? <>已选择 <strong>{selected.length}</strong> 份，单次最多 {BULK_LIMIT} 份</> : <>未选择资料，单次最多 {BULK_LIMIT} 份</>}</span></p></div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {can("item.upload") && <Button size="sm" className="max-sm:h-control-md" onClick={openUploadDialog} disabled={!enabled || !currentFolderId || uploading || folderScanning}><Upload className="size-4" />{folderScanning ? "读取文件夹中…" : "上传文件"}</Button>}
          <Button size="sm" variant="outline" className="max-sm:h-control-md" onClick={() => void load(true)} disabled={loading || refreshing}><RefreshCw className={refreshing ? "size-4 animate-spin" : "size-4"} />{refreshing ? "刷新中…" : "刷新列表"}</Button>
          {selected.length > 1 ? <BatchActionsMenu disabled={bulkDisabled} options={[
            { key: "move", label: "批量移动", icon: <FolderInput className="size-4" />, disabled: !hasMovableSelection, disabledReason: "所选资料中没有当前账号可移动的资料", onSelect: () => { setBulkFailures([]); setBulkMoveFolderId(""); setBulkAction("move"); } },
            { key: "approve", label: "批量确认", icon: <Check className="size-4" />, disabled: !can("item.review") || !hasReviewableSelection, disabledReason: "需要确认权限，且至少选择一份待确认资料", onSelect: () => { setBulkFailures([]); setBulkAction("approve"); } },
            { key: "reject", label: "批量退回", icon: <X className="size-4" />, disabled: !can("item.review") || !hasReviewableSelection, disabledReason: "需要确认权限，且至少选择一份待确认资料", onSelect: () => { setBulkFailures([]); setBulkAction("reject"); } },
            { key: "publish", label: "批量发布", icon: <Rocket className="size-4" />, disabled: !can("item.publish") || !hasPublishableSelection, disabledReason: "需要发布权限，且至少选择一份已确认或发布失败资料", onSelect: () => { setBulkFailures([]); setBulkAction("publish"); } },
            { key: "download", label: "批量下载", icon: <Download className="size-4" />, disabled: !hasDownloadableSelection, disabledReason: "需要下载权限", onSelect: () => { void downloadSelected(); } },
            { key: "archive", label: "批量删除", icon: <Trash2 className="size-4" />, disabled: !hasDeletableSelection, disabledReason: "所选资料中没有当前账号可移入回收站的资料", destructive: true, onSelect: () => openDeleteDialog(selectedItems) },
          ]} /> : (can("folder.request") || can("category.manage")) && <Button size="sm" variant="outline" className="max-sm:h-control-md" onClick={() => can("category.manage") ? setNewFolderOpen(true) : setRequestFolderOpen(true)} disabled={!currentFolder || currentFolder.level >= 4}><FolderPlus className="size-4" />新建目录</Button>}
        </div>
      </div>
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

      <div data-testid="managed-content-drop-list" className="relative" onDragEnter={handleListDragEnter} onDragOver={handleListDragOver} onDragLeave={handleListDragLeave} onDrop={handleListDrop}>
      {listDropActive && <div data-testid="managed-content-drop-overlay" className="pointer-events-none absolute inset-1 z-sticky rounded-ui-lg border-2 border-dashed border-primary/70 bg-background/70 text-center shadow-focus backdrop-blur-[1px]" role="status" aria-live="polite"><div className="absolute left-1/2 flex w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-3" style={{ top: listDropPromptTop }}><span className="flex size-12 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-surface" aria-hidden="true"><Upload className="size-6" /></span><div className="space-y-1"><p className="break-words text-ui-base font-semibold">松开以上传文件到“{currentFolderDropLabel}”</p><p className="text-ui-xs text-muted-foreground">也支持拖入文件夹；支持 PDF、Markdown、Word、Excel 和 PPT 文件</p></div></div></div>}
      {uploadResults.length > 0 && <ul className="border-t border-border px-4 py-3 text-ui-sm sm:px-5" aria-live="polite">{uploadResults.map((entry) => <li key={entry.filename} className="flex items-start justify-between gap-3 border-b border-border py-2 last:border-b-0"><span className="min-w-0"><span className="block break-all">{entry.filename}</span>{entry.reason && <span className="mt-0.5 block break-words text-ui-xs text-muted-foreground">{entry.reason}</span>}</span><Badge className="shrink-0" variant={entry.status === "accepted" ? "success" : "warning"}>{entry.status === "accepted" ? "已接收" : "已跳过"}</Badge></li>)}</ul>}
      {loading ? <LoadingState className="min-h-48 border-x-0 border-b-0" label="正在加载资料…" /> : !error && items.length === 0 ? <EmptyState className="min-h-56 rounded-none border-x-0 border-b-0 sm:min-h-64" title="没有符合条件的资料" description="请调整筛选条件或上传新资料。" /> : !error && <>
        <div className="hidden overflow-x-auto border-t border-border lg:block"><table className="w-full min-w-[68rem] text-ui-sm"><thead className="border-b border-border bg-surface-muted text-left text-muted-foreground"><tr><th className="w-12 px-3 py-3"><Checkbox aria-label="选择当前页前20份资料" checked={allSelected} onChange={toggleAll} /></th>{([ ["title", "资料"], ["updatedAt", "更新时间"], ["status", "状态"], ["source", "来源"] ] as [SortKey, string][]).map(([key, label]) => <th key={key} aria-sort={sort?.key === key ? sort.direction === "asc" ? "ascending" : "descending" : "none"} className="px-3 py-3 font-medium"><button type="button" className="inline-flex items-center gap-1 rounded px-1 py-0.5 hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => toggleSort(key)}>{label}{sortIcon(key)}</button></th>)}<th className="px-3 py-3 text-right font-medium">操作</th></tr></thead><tbody className="divide-y divide-border">{sortedItems.map((item, index) => { const movable = canMoveItem(item); return <tr key={item.item_id} draggable={movable} title={movable ? "拖动到上方文件夹可移动资料" : undefined} onDragStart={() => setDraggedItem(item)} onDragEnd={() => setDraggedItem(null)} className={`transition-colors duration-normal hover:bg-surface-muted/60 ${movable ? "cursor-grab" : ""}`}><td className="px-3 py-3"><Checkbox aria-label={`选择${item.title}`} checked={selected.includes(item.version_id)} disabled={index >= BULK_LIMIT} onChange={() => setSelected((current) => current.includes(item.version_id) ? current.filter((id) => id !== item.version_id) : [...current, item.version_id].slice(0, BULK_LIMIT))} /></td><td className="max-w-xs px-3 py-3"><p className="break-words font-medium">{item.title}</p><p className="mt-0.5 break-all text-ui-xs text-muted-foreground">{item.original_filename} · v{item.version_number}</p></td><td className="whitespace-nowrap px-3 py-3 tabular-nums">{formatManagedUpdatedAt(item.updated_at)}</td><td className="px-3 py-3"><Badge variant={statusVariant(item.lifecycle_status)}>{statusLabel[item.lifecycle_status] || "未知状态"}</Badge></td><td className="px-3 py-3">{sourceLabel[item.source_origin] || "其他来源"}</td><td className="px-3 py-3 text-right">{renderActions(item)}</td></tr>; })}</tbody></table></div>
        <ul className="divide-y divide-border border-t border-border lg:hidden">{items.map((item, index) => <li key={item.item_id} className="space-y-3 px-4 py-4 sm:px-5"><div className="flex items-start gap-3"><Checkbox className="mt-0.5" aria-label={`选择${item.title}`} checked={selected.includes(item.version_id)} disabled={index >= BULK_LIMIT} onChange={() => setSelected((current) => current.includes(item.version_id) ? current.filter((id) => id !== item.version_id) : [...current, item.version_id].slice(0, BULK_LIMIT))} /><div className="min-w-0 flex-1"><div className="flex items-start justify-between gap-3"><p className="break-words font-medium">{item.title}</p><Badge className="shrink-0" variant={statusVariant(item.lifecycle_status)}>{statusLabel[item.lifecycle_status] || "未知状态"}</Badge></div><p className="mt-1 break-all text-ui-xs text-muted-foreground">{item.original_filename} · v{item.version_number}</p></div></div><dl className="grid grid-cols-[4rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-ui-sm"><dt className="text-muted-foreground">更新时间</dt><dd className="whitespace-nowrap tabular-nums">{formatManagedUpdatedAt(item.updated_at)}</dd><dt className="text-muted-foreground">来源</dt><dd>{sourceLabel[item.source_origin] || "其他来源"}</dd></dl>{renderActions(item)}</li>)}</ul>
        <div className="flex flex-col gap-2 border-t border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5"><p className="text-ui-xs text-muted-foreground">共 {total} 份，第 {page + 1} / {pageCount} 页</p><div className="flex flex-wrap items-center justify-end gap-2"><label className="flex items-center gap-2 text-ui-xs text-muted-foreground">每页<Select aria-label="每页条数" className="h-control-sm w-20" value={String(pageSize)} onChange={(event) => setPageSize(Number(event.target.value))}>{PAGE_SIZE_OPTIONS.map((size) => <option key={size} value={size}>{size} 条</option>)}</Select></label><Button size="sm" variant="outline" disabled={page === 0 || loading} onClick={() => setPage((value) => value - 1)}>上一页</Button><Select aria-label="跳转页码" className="h-control-sm w-24" value={String(page + 1)} onChange={(event) => setPage(Number(event.target.value) - 1)} disabled={loading}>{Array.from({ length: pageCount }, (_, index) => <option key={index + 1} value={index + 1}>第 {index + 1} 页</option>)}</Select><Button size="sm" variant="outline" disabled={page + 1 >= pageCount || loading} onClick={() => setPage((value) => value + 1)}>下一页</Button></div></div>
      </>}
      </div>
    </Card>

    <Dialog open={Boolean(bulkAction && bulkAction !== "archive")} onOpenChange={(open) => { if (!open) { setBulkAction(null); setBulkFailures([]); } }}><DialogContent><DialogHeader><DialogTitle>{bulkAction === "move" ? "批量移动资料" : bulkAction === "publish" ? "批量发布资料" : bulkAction === "reject" ? "批量退回资料" : "批量确认资料"}</DialogTitle><DialogDescription>已选择 {selectedItems.length} 份资料。系统会逐项执行，并保留不符合状态或权限要求的失败原因。</DialogDescription></DialogHeader>{bulkAction === "move" && <label className="space-y-1.5 text-ui-sm font-medium"><span>目标目录</span><Select value={bulkMoveFolderId} onChange={(event) => setBulkMoveFolderId(event.target.value)}><option value="">请选择目标目录</option>{categories.filter((category) => category.is_active).map((category) => <option key={category.id} value={category.id}>{category.full_path || `${category.display_code} ${category.display_name}`}</option>)}</Select></label>}{bulkFailures.length > 0 && <div className="space-y-2 text-ui-sm text-destructive" role="alert"><p>上次操作有 {bulkFailures.length} 份失败：</p><ul className="max-h-48 space-y-1 overflow-y-auto border-y border-destructive/30 py-2">{bulkFailures.map((entry) => <li key={entry.version_id} className="break-words"><span className="font-medium">{entry.title}</span>{entry.message ? `：${entry.message}` : "：请刷新后重试"}</li>)}</ul></div>}<DialogFooter><Button variant="outline" onClick={() => setBulkAction(null)} disabled={busyAction === "bulk"}>取消</Button><Button onClick={() => void executeBulk()} disabled={busyAction === "bulk" || selectedItems.length === 0 || (bulkAction === "move" && !bulkMoveFolderId)}>{busyAction === "bulk" ? "处理中…" : bulkFailures.length ? "重试失败项" : "确认执行"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={Boolean(detail) && !previewState.parentId} onOpenChange={(open) => { if (!open && !previewState.parentId) setDetail(null); }}><DialogContent className="max-w-2xl"><DialogHeader><DialogTitle>{detail?.title || "资料详情"}</DialogTitle><DialogDescription>核对文件、分类、来源和版本后再确认或发布。</DialogDescription></DialogHeader>{detail && <div className="space-y-4"><PublicationFailure item={detail} /><dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-2 text-ui-sm [&_dt]:whitespace-nowrap"><dt className="text-muted-foreground">文件名</dt><dd className="break-all">{detail.original_filename}</dd><dt className="text-muted-foreground">分类</dt><dd className="break-words">{detail.category_path || detail.category_label}</dd><dt className="text-muted-foreground">状态</dt><dd><Badge variant={statusVariant(detail.lifecycle_status)}>{statusLabel[detail.lifecycle_status]}</Badge></dd><dt className="text-muted-foreground">来源</dt><dd>{sourceLabel[detail.source_origin] || "其他来源"}</dd><dt className="text-muted-foreground">版本</dt><dd>v{detail.version_number}</dd><dt className="text-muted-foreground">创建时间</dt><dd>{formatAdminDate(detail.created_at)}</dd><dt className="text-muted-foreground">最后更新时间</dt><dd className="whitespace-nowrap">{formatAdminDate(detail.updated_at)}</dd><dt className="text-muted-foreground">发布尝试</dt><dd>共 {detail.publication_attempt_count} 次</dd></dl><div className="flex flex-wrap gap-2">{detail.preview_parent_id && ["pdf", "docx", "xlsx", "pptx"].includes(detail.doc_type) ? <Button variant="outline" onClick={() => { openDocumentPreview(detail.preview_parent_id!, detail.title, detail.doc_type, 1, {}, "managed-content-detail"); }}><Eye className="size-4" />预览文件</Button> : detail.doc_type === "pdf" || can("item.download") ? <a className={buttonVariants({ variant: "outline" })} href={adminContentApi.fileUrl(detail.version_id)} target="_blank" rel="noreferrer"><Eye className="size-4" />打开文件</a> : <Button variant="outline" disabled title="打开文件（需要下载权限）"><Eye className="size-4" />打开文件</Button>}{can("item.submit") && ["draft", "rejected"].includes(detail.lifecycle_status) && <Button onClick={() => void act(detail, "submit", () => adminContentApi.submit(detail.version_id), "已提交确认")} disabled={Boolean(busyAction)}><Send className="size-4" />{busyAction === `${detail.version_id}:submit` ? "提交中…" : "提交确认"}</Button>}{can("item.review") && detail.lifecycle_status === "awaiting_review" && <><Button onClick={() => void act(detail, "approve", () => adminContentApi.review(detail.version_id, true), "资料已确认")} disabled={Boolean(busyAction)}><Check className="size-4" />{busyAction === `${detail.version_id}:approve` ? "确认中…" : "确认"}</Button><Button variant="outline" onClick={() => void act(detail, "reject", () => adminContentApi.review(detail.version_id, false), "资料已退回")} disabled={Boolean(busyAction)}><X className="size-4" />{busyAction === `${detail.version_id}:reject` ? "退回中…" : "退回"}</Button></>}{can("item.publish") && ["approved", "publication_failed"].includes(detail.lifecycle_status) && <Button onClick={() => void act(detail, "publish", () => adminContentApi.publish(detail.version_id), "已进入发布队列")} disabled={Boolean(busyAction)}><Rocket className="size-4" />{busyAction === `${detail.version_id}:publish` ? "发布中…" : detail.lifecycle_status === "publication_failed" ? "重新发布" : "发布"}</Button>}</div></div>}</DialogContent></Dialog>

    <Dialog open={requestFolderOpen} onOpenChange={setRequestFolderOpen}><DialogContent><DialogHeader><DialogTitle>申请新建文件夹</DialogTitle><DialogDescription>申请将在“{currentFolder?.display_name || "当前目录"}”下创建受控目录，由资料负责人审批。</DialogDescription></DialogHeader><label className="space-y-1.5 text-ui-sm font-medium"><span>文件夹名称</span><Input value={requestFolderName} onChange={(event) => setRequestFolderName(event.target.value)} placeholder="例如：净高分析" autoFocus /></label><DialogFooter><Button variant="outline" onClick={() => setRequestFolderOpen(false)} disabled={busyAction === "request-folder"}>取消</Button><Button onClick={() => void requestFolder()} disabled={!requestFolderName.trim() || busyAction === "request-folder"}>{busyAction === "request-folder" ? "提交中…" : "提交申请"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={newFolderOpen} onOpenChange={setNewFolderOpen}><DialogContent><DialogHeader><DialogTitle>新建文件夹</DialogTitle><DialogDescription>文件夹将建立在“{currentFolder?.display_name || "当前目录"}”下，最多支持四级目录。</DialogDescription></DialogHeader><label className="space-y-1.5 text-ui-sm font-medium"><span>文件夹名称</span><Input value={newFolderName} onChange={(event) => setNewFolderName(event.target.value)} placeholder="例如：净高分析" autoFocus /></label><DialogFooter><Button variant="outline" onClick={() => setNewFolderOpen(false)} disabled={busyAction === "new-folder"}>取消</Button><Button onClick={() => void createFolder()} disabled={!newFolderName.trim() || busyAction === "new-folder"}>{busyAction === "new-folder" ? "创建中…" : "创建"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={Boolean(moveTarget)} onOpenChange={(open) => { if (!open) setMoveTarget(null); }}><DialogContent><DialogHeader><DialogTitle>移动资料</DialogTitle><DialogDescription>将“{moveTarget?.title || "资料"}”移动到另一个受控目录。已确认或已发布资料需要先退回。</DialogDescription></DialogHeader><label className="space-y-1.5 text-ui-sm font-medium"><span>目标目录</span><Select value={moveFolderId} onChange={(event) => setMoveFolderId(event.target.value)}>{categories.filter((category) => category.is_active).map((category) => <option key={category.id} value={category.id}>{category.full_path || `${category.display_code} ${category.display_name}`}</option>)}</Select></label><DialogFooter><Button variant="outline" onClick={() => setMoveTarget(null)} disabled={Boolean(busyAction?.endsWith(":move"))}>取消</Button><Button onClick={() => void moveContent()} disabled={!moveFolderId || moveFolderId === moveTarget?.category_id || Boolean(busyAction?.endsWith(":move"))}>{busyAction?.endsWith(":move") ? "移动中…" : "移动"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={Boolean(renameTarget)} onOpenChange={(open) => { if (!open && busyAction !== "rename") { setRenameTarget(null); setRenameConflict(null); setRenameError(null); } }}><DialogContent><DialogHeader><DialogTitle>重命名资料</DialogTitle><DialogDescription>标题和源文件名会作为新草稿版本保存，之后需要重新确认并发布。</DialogDescription></DialogHeader><div className="space-y-3"><label className="block space-y-1.5 text-ui-sm font-medium"><span>资料标题</span><Input value={renameTitle} onChange={(event) => { setRenameTitle(event.target.value); setRenameConflict(null); }} /></label><label className="block space-y-1.5 text-ui-sm font-medium"><span>源文件名</span><Input value={renameFilename} onChange={(event) => { setRenameFilename(event.target.value); setRenameConflict(null); }} /><span className="block text-ui-xs font-normal text-muted-foreground">只能修改名称，不能改变文件扩展名。</span></label>{renameConflict && <div className="space-y-2 rounded-ui-md border border-warning/50 bg-warning/10 p-3 text-ui-sm" role="alert"><p className="font-medium">当前目录存在同名资料，是否替换？</p><p className="break-words">{renameConflict.title}（{renameConflict.original_filename}）</p><p className="text-muted-foreground">替换会将上述资料移入回收站并立即停止检索；当前资料的新版本仍需重新确认和发布。</p></div>}{renameError && <p className="text-ui-sm text-destructive" role="alert">{renameError}</p>}</div><DialogFooter><Button variant="outline" disabled={busyAction === "rename"} onClick={() => setRenameTarget(null)}>取消</Button>{renameConflict ? <Button variant="destructive" disabled={busyAction === "rename"} onClick={() => void renameContent(true)}>{busyAction === "rename" ? "替换中…" : "确认替换并重命名"}</Button> : <Button disabled={busyAction === "rename" || !renameTitle.trim() || !renameFilename.trim()} onClick={() => void renameContent()}>{busyAction === "rename" ? "保存中…" : "保存为新版本"}</Button>}</DialogFooter></DialogContent></Dialog>

    <Dialog open={Boolean(updateTarget)} onOpenChange={(open) => { if (!open && busyAction !== "update") { setUpdateTarget(null); setUpdateConflict(null); setUpdateError(null); setUpdateFile(null); } }}><DialogContent><DialogHeader><DialogTitle>更新资料文件</DialogTitle><DialogDescription>上传替换文件后会创建新草稿版本，旧发布版本会继续检索，直到新版本发布成功。</DialogDescription></DialogHeader><div className="space-y-3"><label className="flex min-h-32 cursor-pointer flex-col items-center justify-center gap-2 rounded-ui-lg border border-dashed border-input bg-background px-4 py-5 text-center hover:bg-surface-muted focus-within:ring-2 focus-within:ring-ring"><FileUp className="size-6 text-primary" /><span className="text-ui-sm font-medium">{updateFile ? updateFile.name : "选择替换文件"}</span><span className="text-ui-xs text-muted-foreground">支持 PDF、Markdown、Word、Excel 和 PPT</span><input ref={updateFileInputRef} type="file" className="sr-only" aria-label="选择替换文件" accept=".pdf,.md,.docx,.xlsx,.pptx" onChange={(event) => { setUpdateFile(event.target.files?.[0] || null); setUpdateConflict(null); }} /></label>{updateFile && <div className="space-y-2"><p className="text-ui-sm font-medium">文件名处理</p><div className="grid grid-cols-2 gap-2" role="group" aria-label="文件名处理"><Button type="button" variant={updateFilenameMode === "old" ? "default" : "outline"} aria-pressed={updateFilenameMode === "old"} onClick={() => { setUpdateFilenameMode("old"); setUpdateConflict(null); }}>沿用原名称</Button><Button type="button" variant={updateFilenameMode === "new" ? "default" : "outline"} aria-pressed={updateFilenameMode === "new"} onClick={() => { setUpdateFilenameMode("new"); setUpdateConflict(null); }}>使用新文件名</Button></div><p className="break-all text-ui-xs text-muted-foreground">{updateFilenameMode === "old" ? `将使用原名称并匹配新格式：${filenameForOldMode(updateTarget?.original_filename || "", updateFile.name)}` : `将使用：${updateFile.name}`}</p></div>}{updateConflict && <div className="space-y-2 rounded-ui-md border border-warning/50 bg-warning/10 p-3 text-ui-sm" role="alert"><p className="font-medium">当前目录存在同名资料，是否替换？</p><p>{updateConflict.title}（{updateConflict.original_filename}）</p><p className="text-muted-foreground">替换会将上述资料移入回收站并停止检索。</p></div>}{updateError && <p className="text-ui-sm text-destructive" role="alert">{updateError}</p>}</div><DialogFooter><Button variant="outline" disabled={busyAction === "update"} onClick={() => setUpdateTarget(null)}>取消</Button>{updateConflict ? <Button variant="destructive" disabled={busyAction === "update"} onClick={() => void updateContent(true)}>{busyAction === "update" ? "替换中…" : "确认替换并更新"}</Button> : <Button disabled={!updateFile || busyAction === "update"} onClick={() => void updateContent()}>{busyAction === "update" ? "上传中…" : "确认更新"}</Button>}</DialogFooter></DialogContent></Dialog>

    <Dialog open={uploadDialogOpen} onOpenChange={(open) => { if (!open) closeUploadDialog(); }}><DialogContent><DialogHeader><DialogTitle>上传文件</DialogTitle><DialogDescription>文件将上传到当前目录“{currentFolder?.full_path || "请选择目录"}”，上传后先进入待提交状态。</DialogDescription></DialogHeader><label onDragEnter={(event) => { event.preventDefault(); setDragActive(true); }} onDragOver={(event) => event.preventDefault()} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragActive(false); }} onDrop={(event) => { event.preventDefault(); setDragActive(false); void inspectDroppedUpload(event.dataTransfer, "select"); }} className={`flex min-h-36 cursor-pointer flex-col items-center justify-center gap-2 rounded-ui-lg border border-dashed px-4 py-6 text-center transition-colors duration-normal focus-within:ring-2 focus-within:ring-ring ${dragActive ? "border-primary bg-primary/5" : "border-input bg-background hover:bg-surface-muted"}`}><Upload className="size-6 text-primary" /><span className="text-ui-sm font-medium">{folderScanning ? "正在读取文件夹…" : "拖动文件到这里，或选择文件"}</span><span className="text-ui-xs text-muted-foreground">支持 PDF、Markdown、Word、Excel 和 PPT</span><input ref={fileInputRef} aria-label="选择资料文件" type="file" multiple accept=".pdf,.md,.docx,.xlsx,.pptx" className="sr-only" disabled={uploading || folderScanning} onChange={(event) => acceptFiles(Array.from(event.target.files || []))} /></label><Button type="button" variant="outline" className="w-full" onClick={() => folderInputRef.current?.click()} disabled={uploading || folderScanning}><Folder className="size-4" />上传文件夹</Button><input ref={folderInputRef} aria-label="选择资料文件夹" type="file" multiple className="sr-only" disabled={uploading || folderScanning} onChange={(event) => selectFolder(Array.from(event.target.files || []))} {...({ webkitdirectory: "", directory: "" } as React.InputHTMLAttributes<HTMLInputElement>)} />{files.length > 0 && <ul className="max-h-40 space-y-1 overflow-y-auto rounded-ui-md border border-border px-3 py-2 text-ui-sm">{files.map((file) => <li key={`${file.name}-${file.size}`} className="break-all">{file.name}<span className="ml-2 text-ui-xs text-muted-foreground">{formatUploadSize(file.size)}</span></li>)}</ul>}<DialogFooter><Button variant="outline" onClick={closeUploadDialog} disabled={uploading || folderScanning}>取消</Button><Button onClick={() => void confirmDialogUpload()} disabled={!files.length || uploading || folderScanning || !currentFolderId}>{uploading ? "上传中…" : "确定上传"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={pendingUploadFiles.length > 0} onOpenChange={(open) => { if (!open && !uploading) { setPendingUploadFiles([]); setPendingUploadFolderId(""); } }}><DialogContent><DialogHeader><DialogTitle>确认上传</DialogTitle><DialogDescription>将上传到“{categories.find((category) => category.id === pendingUploadFolderId)?.full_path || currentFolder?.full_path || "当前目录"}”，确认后文件会进入待提交状态。</DialogDescription></DialogHeader><div className="space-y-2 text-ui-sm"><p>共 {pendingUploadFiles.length} 个文件</p><ul className="max-h-48 space-y-1 overflow-y-auto rounded-ui-md border border-border px-3 py-2">{pendingUploadFiles.map((file) => <li key={`${file.name}-${file.size}`} className="break-all">{file.name}<span className="ml-2 text-ui-xs text-muted-foreground">{formatUploadSize(file.size)}</span></li>)}</ul></div><DialogFooter><Button variant="outline" onClick={() => { setPendingUploadFiles([]); setPendingUploadFolderId(""); }} disabled={uploading}>取消</Button><Button onClick={() => void confirmFileDropUpload()} disabled={uploading}>{uploading ? "上传中…" : "确定上传"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={Boolean(pendingFolderUpload)} onOpenChange={(open) => { if (!open && !uploading) { setPendingFolderUpload(null); setPendingFolderUploadFolderId(""); } }}><DialogContent className="max-w-2xl"><DialogHeader><DialogTitle>上传文件夹</DialogTitle><DialogDescription>确认后将按相对路径上传到“{categories.find((category) => category.id === pendingFolderUploadFolderId)?.full_path || currentFolder?.full_path || "当前目录"}”。缺少的目录仍按当前账号权限创建，文件上传后进入待提交状态。</DialogDescription></DialogHeader>{pendingFolderUpload && <div className="space-y-4 text-ui-sm"><dl className="grid grid-cols-2 gap-2 rounded-ui-md border border-border bg-surface-muted/40 p-3 sm:grid-cols-4"><div className="col-span-2 sm:col-span-4"><dt className="text-ui-xs text-muted-foreground">根文件夹</dt><dd className="mt-1 break-all font-medium">{pendingFolderUpload.rootFolderNames.length > 1 ? `${pendingFolderUpload.rootFolderNames[0]} 等 ${pendingFolderUpload.rootFolderNames.length} 个根文件夹` : pendingFolderUpload.rootFolderNames[0] || "所选文件夹"}</dd></div><div><dt className="text-ui-xs text-muted-foreground">文件夹</dt><dd className="mt-1 font-medium tabular-nums">{pendingFolderUpload.folderCount} 个</dd></div><div><dt className="text-ui-xs text-muted-foreground">可上传文件</dt><dd className="mt-1 font-medium tabular-nums">{pendingFolderUpload.fileCount} 个</dd></div><div><dt className="text-ui-xs text-muted-foreground">已忽略</dt><dd className="mt-1 font-medium tabular-nums">{pendingFolderUpload.ignoredEntries.length} 个</dd></div><div><dt className="text-ui-xs text-muted-foreground">上传大小</dt><dd className="mt-1 font-medium tabular-nums">{formatUploadSize(pendingFolderUpload.totalSize)}</dd></div></dl>{pendingFolderUpload.ignoredEntries.length > 0 && <div className="space-y-1"><p className="text-ui-xs font-medium text-muted-foreground">以下格式不受支持，将被忽略</p><ul className="max-h-24 space-y-1 overflow-y-auto rounded-ui-md border border-warning/40 bg-warning/10 px-3 py-2 text-ui-xs">{pendingFolderUpload.ignoredEntries.map((entry) => <li key={entry.relativePath} className="break-all">{entry.relativePath}</li>)}</ul></div>}<div className="space-y-1"><p className="text-ui-xs font-medium text-muted-foreground">将上传的文件</p><ul className="max-h-40 space-y-1 overflow-y-auto rounded-ui-md border border-border px-3 py-2">{pendingFolderUpload.entries.map((entry) => <li key={entry.relativePath} className="flex items-start justify-between gap-3"><span className="min-w-0 break-all">{entry.relativePath}</span><span className="shrink-0 text-ui-xs text-muted-foreground">{formatUploadSize(entry.file.size)}</span></li>)}</ul></div></div>}<DialogFooter><Button variant="outline" onClick={() => { setPendingFolderUpload(null); setPendingFolderUploadFolderId(""); }} disabled={uploading}>取消</Button><Button onClick={() => void confirmFolderUpload()} disabled={uploading || !pendingFolderUpload?.fileCount}>{uploading ? "上传中…" : "开始上传"}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={deleteTargets.length > 0} onOpenChange={(open) => { if (!open && busyAction !== "archive") { setDeleteTargets([]); setDeleteAcknowledged(false); setDeleteError(null); } }}><DialogContent><DialogHeader><DialogTitle>{deleteTargets.length > 1 ? `将 ${deleteTargets.length} 份资料移入回收站？` : "将资料移入回收站？"}</DialogTitle><DialogDescription>以下资料将立即停止进入知识库检索。文件、版本及审核发布历史会保留，可从回收站恢复。</DialogDescription></DialogHeader><ul className="max-h-48 space-y-2 overflow-y-auto rounded-ui-md border border-border p-3 text-ui-sm">{deleteTargets.map((item) => <li key={item.item_id} className="min-w-0"><p className="break-words font-medium">{item.title}</p><p className="break-all text-ui-xs text-muted-foreground">{item.original_filename}</p></li>)}</ul><label className="flex items-start gap-2 rounded-ui-md border border-destructive/30 bg-destructive/5 p-3 text-ui-sm"><Checkbox className="mt-0.5" checked={deleteAcknowledged} onChange={(event) => setDeleteAcknowledged(event.target.checked)} /><span>我已了解这些资料移入回收站后将不再进入检索。</span></label>{deleteError && <p className="rounded-ui-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui-sm text-destructive" role="alert">{deleteError}</p>}<DialogFooter><Button variant="outline" disabled={busyAction === "archive"} onClick={() => setDeleteTargets([])}>取消</Button><Button variant="destructive" disabled={busyAction === "archive" || !deleteAcknowledged} onClick={() => void deleteContent()}>{busyAction === "archive" ? "处理中…" : "确认移入回收站"}</Button></DialogFooter></DialogContent></Dialog>
  </section>;
}
