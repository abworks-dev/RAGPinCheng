import { useCallback, useEffect, useRef, useState } from "react";
import { Archive, ArrowLeft, Ban, CheckCircle2, ChevronDown, ClipboardCheck, FileUp, Film, FolderInput, LoaderCircle, RefreshCcw, RefreshCw, Repeat2, RotateCcw, Rocket, Search, Settings2, Trash2, Upload, XCircle } from "lucide-react";
import { adminMediaApi } from "../../api/admin/media";
import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button, buttonVariants } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { Input } from "../../components/ui/input";
import { LoadingState } from "../../components/ui/loading-state";
import { Progress } from "../../components/ui/progress";
import { Select } from "../../components/ui/select";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { Checkbox } from "../../components/ui/checkbox";
import { IconButton } from "../../components/ui/icon-button";
import { TranscriptionWorkbenchSheet } from "../../components/TranscriptionWorkbenchSheet";
import { ManagedSummaryCard } from "../../components/admin/ManagedSummaryCard";
import { CategoryTreePicker } from "../../components/admin/CategoryTreePicker";
import { useTranscriptionJobs } from "../../hooks/useTranscriptionJobs";
import { useAdminMediaAssets } from "../../hooks/useAdminMediaAssets";
import { createRequestId } from "../../lib/request-id";
import type { ManagedCategory, MediaAsset, MediaUploadPreflightEntry, TranscriptionJob, TranscriptionSchemeOption } from "../../types";
import { formatAdminDate, formatBytes } from "../../lib/admin-formatters";
import { toast } from "../../components/ui/toast";

type UploadMode = "manual" | "automatic";
type UploadState = "waiting" | "uploading" | "preparing" | "succeeded" | "skipped" | "failed";
type StatusVariant = "secondary" | "success" | "warning" | "destructive" | "info";
type MediaFilter = "all" | "processing" | "review" | "publishing" | "failed";

type PendingVideo = {
  id: string;
  file: File;
  title: string;
  originalFilename: string;
  selected: boolean;
  profileId: string;
  transcriptFile: File | null;
  transcriptText: string | null;
  requestId: string;
  state: UploadState;
  transferRatio: number;
  error: string | null;
  replacementSourceMediaId: string | null;
};

type MediaConflictChoice = {
  strategy: "skip" | "rename" | "update";
  title: string;
  originalFilename: string;
};

const mediaStatusMeta: Record<string, { label: string; variant: StatusVariant }> = {
  uploaded: { label: "已上传", variant: "secondary" },
  uploading: { label: "上传中", variant: "info" },
  transcribing: { label: "自动转录中", variant: "warning" },
  transcript_ready: { label: "转写草稿就绪", variant: "info" },
  indexing: { label: "索引中", variant: "warning" },
  ready: { label: "已就绪", variant: "success" },
  failed: { label: "失败", variant: "destructive" },
};

const reviewMeta: Record<string, { label: string; variant: StatusVariant }> = {
  not_required: { label: "无需审核", variant: "secondary" },
  awaiting_review: { label: "待人工审核", variant: "warning" },
  review_approved: { label: "审核通过", variant: "success" },
  review_rejected: { label: "审核驳回", variant: "destructive" },
};

const publicationMeta: Record<string, { label: string; variant: StatusVariant }> = {
  not_published: { label: "未发布", variant: "secondary" },
  publishing: { label: "发布中", variant: "warning" },
  published: { label: "已发布", variant: "success" },
  publication_failed: { label: "发布失败", variant: "destructive" },
};

const stageLabels: Record<string, string> = {
  preparing_audio: "准备音轨",
  validating_input: "校验输入",
  transcribing: "转录中",
  normalizing: "规范化",
  formatting: "生成 Markdown",
};
const activeJobStatuses = new Set(["pending", "running"]);

const jobStatusMeta: Record<string, { label: string; variant: StatusVariant }> = {
  pending: { label: "等待转录", variant: "secondary" },
  running: { label: "正在转录", variant: "warning" },
  succeeded: { label: "转录完成", variant: "success" },
  failed: { label: "转录失败", variant: "destructive" },
  cancelled: { label: "已取消", variant: "secondary" },
};

function StatusBadge({ value, meta, empty = "未开始" }: {
  value: string | null | undefined;
  meta: Record<string, { label: string; variant: StatusVariant }>;
  empty?: string;
}) {
  const item = value ? meta[value] : null;
  return <Badge variant={item?.variant ?? "secondary"}>{item?.label ?? value ?? empty}</Badge>;
}

function formatDuration(milliseconds: number) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainingSeconds = seconds % 60;
  if (hours > 0) return `${hours}小时${minutes}分`;
  if (minutes > 0) return `${minutes}分${remainingSeconds}秒`;
  return `${remainingSeconds}秒`;
}

function JobSummary({ job }: { job: TranscriptionJob }) {
  if (job.status === "succeeded") return <p className="mt-1 text-ui-xs text-muted-foreground">草稿已生成，等待后续审核与发布。</p>;
  if (job.status === "failed") return <p className="mt-1 text-ui-xs text-destructive">{job.failure?.message || job.error_summary || job.failure_error_code || "转录失败"}</p>;
  if (job.status === "cancelled") return <p className="mt-1 text-ui-xs text-muted-foreground">任务已取消，可重新转录。</p>;
  const stage = job.stage ? stageLabels[job.stage] || job.stage : "排队中";
  if (job.status === "pending") return <p className="mt-1 text-ui-xs text-muted-foreground">{stage}{job.stage === "preparing_audio" ? "" : " · 等待服务调度"}</p>;

  const elapsedMs = Math.max(0, Date.now() - (job.started_at ?? job.created_at) * 1000);
  const totalMs = job.total_ms;
  const hasCheckpoint = totalMs != null && totalMs > 0 && job.processed_ms > 0 && job.processed_ms < totalMs;
  const progress = hasCheckpoint ? Math.min(100, Math.round((job.processed_ms / totalMs) * 100)) : null;
  const isTranscribing = job.stage === "transcribing";
  const durationDetail = totalMs != null && totalMs > 0
    ? `视频时长 ${formatDuration(totalMs)}`
    : "正在读取视频时长";
  const detail = hasCheckpoint && totalMs != null
    ? `${formatDuration(job.processed_ms)} / ${formatDuration(totalMs)}`
    : isTranscribing
      ? `模型整段处理中 · ${durationDetail} · 已耗时 ${formatDuration(elapsedMs)}`
      : "正在完成结果处理";
  return (
    <div className="mt-1 space-y-1.5 text-ui-xs text-muted-foreground">
      <p>{stage} · {progress === null ? detail : `${progress}% · ${detail}`}</p>
      <Progress label={`转录进度：${stage}`} value={progress} />
    </div>
  );
}

function LifecycleRail({ asset }: { asset: MediaAsset }) {
  const stages = [
    { label: "审核", value: asset.review_status, meta: reviewMeta },
    { label: "发布", value: asset.publication_status, meta: publicationMeta },
  ];
  return (
    <ol className="grid gap-1.5 sm:grid-cols-2" aria-label="审核、发布流程">
      {stages.map((stage) => (
        <li key={stage.label} className="flex min-w-0 items-center justify-between gap-2 rounded-ui-sm border border-border/70 bg-background/60 px-2 py-1.5" aria-label={`${stage.label}：${stage.value ? stage.meta[stage.value]?.label || stage.value : "未开始"}`}>
          <span className="text-ui-xs font-medium text-muted-foreground">{stage.label}</span>
          <StatusBadge value={stage.value} meta={stage.meta} />
        </li>
      ))}
    </ol>
  );
}

function pendingFromFile(file: File, profileId: string): PendingVideo {
  return {
    id: createRequestId(),
    file,
    title: file.name.replace(/\.[^.]+$/, ""),
    originalFilename: file.name,
    selected: true,
    profileId,
    transcriptFile: null,
    transcriptText: null,
    requestId: createRequestId(),
    state: "waiting",
    transferRatio: 0,
    error: null,
    replacementSourceMediaId: null,
  };
}

export function AdminMediaPage({ embedded = false }: { embedded?: boolean }) {
  const [deletingMediaId, setDeletingMediaId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<MediaAsset | null>(null);
  const [archiveTarget, setArchiveTarget] = useState<MediaAsset | null>(null);
  const [archiveAcknowledged, setArchiveAcknowledged] = useState(false);
  const [moveTarget, setMoveTarget] = useState<MediaAsset | null>(null);
  const [moveCategoryId, setMoveCategoryId] = useState("");
  const [moveBusy, setMoveBusy] = useState(false);
  const [moveError, setMoveError] = useState<string | null>(null);
  const [schemes, setSchemes] = useState<TranscriptionSchemeOption[]>([]);
  const [schemeError, setSchemeError] = useState<string | null>(null);
  const [step, setStep] = useState(1);
  const [mode, setMode] = useState<UploadMode | null>(null);
  const [pending, setPending] = useState<PendingVideo[]>([]);
  const [bulkProfileId, setBulkProfileId] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [activeBatchIds, setActiveBatchIds] = useState<string[]>([]);
  const [dragging, setDragging] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [discardPromptOpen, setDiscardPromptOpen] = useState(false);
  const [discardPromptMode, setDiscardPromptMode] = useState<"close" | "cancel">("close");
  const [categories, setCategories] = useState<ManagedCategory[]>([]);
  const [targetCategoryId, setTargetCategoryId] = useState("cat-05");
  const [conflictReview, setConflictReview] = useState<MediaUploadPreflightEntry[] | null>(null);
  const [conflictChoices, setConflictChoices] = useState<Record<string, MediaConflictChoice>>({});
  const [mediaFilter, setMediaFilter] = useState<MediaFilter>("all");
  const [mediaQuery, setMediaQuery] = useState("");
  const [mediaPage, setMediaPage] = useState(0);
  const [mediaPageSize, setMediaPageSize] = useState(10);
  const [selectedMediaIds, setSelectedMediaIds] = useState<string[]>([]);
  const [batchMenuOpen, setBatchMenuOpen] = useState(false);
  const [batchCleanupTargetIds, setBatchCleanupTargetIds] = useState<string[]>([]);
  const [batchActionBusy, setBatchActionBusy] = useState(false);
  const [startDialogMediaIds, setStartDialogMediaIds] = useState<string[]>(() => {
    if (typeof window === "undefined") return [];
    const params = new URLSearchParams(window.location.search);
    const mediaId = params.get("media_id");
    return params.get("action") === "start-transcription" && mediaId ? [mediaId] : [];
  });
  const [startDefaultSchemeId, setStartDefaultSchemeId] = useState("");
  const [startSchemeOverrides, setStartSchemeOverrides] = useState<Record<string, string>>({});
  const [startBusy, setStartBusy] = useState(false);
  const [reTranscribeMediaIds, setReTranscribeMediaIds] = useState<string[]>([]);
  const [reTranscribeOverrides, setReTranscribeOverrides] = useState<Record<string, string>>({});
  const [reTranscribeBusy, setReTranscribeBusy] = useState(false);
  const [lastLoadedAt, setLastLoadedAt] = useState<number | null>(null);
  const [selectedMediaId, setSelectedMediaId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    const params = new URLSearchParams(window.location.search);
    return params.get("workbench") === "1" ? params.get("media_id") : null;
  });
  const [workbenchAction, setWorkbenchAction] = useState<"edit-current" | null>(() => {
    if (typeof window === "undefined") return null;
    return new URLSearchParams(window.location.search).get("action") === "edit-current" ? "edit-current" : null;
  });
  const [workbenchVersionId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return new URLSearchParams(window.location.search).get("version_id");
  });
  const [replaceSourceMediaId, setReplaceSourceMediaId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    const params = new URLSearchParams(window.location.search);
    return params.get("action") === "replace" ? params.get("media_id") : null;
  });
  const [replacementFile, setReplacementFile] = useState<File | null>(null);
  const [replacementProfileId, setReplacementProfileId] = useState("");
  const [replacementProgress, setReplacementProgress] = useState(0);
  const [replacementBusy, setReplacementBusy] = useState(false);
  const [replacementError, setReplacementError] = useState<string | null>(null);
  const errorAlertRef = useRef<HTMLDivElement>(null);
  const retryIdempotencyKeys = useRef(new Map<string, string>());
  const previousJobStatuses = useRef(new Map<string, string>());
  const { assets: mediaAssets, loading, error: loadError, refresh, removeAsset } = useAdminMediaAssets();
  const { jobs, jobsByMediaId, error: jobsError, refreshJobs, replaceJob } = useTranscriptionJobs();
  useEffect(() => {
    const currentStatuses = new Map(jobs.map((job) => [job.job_id, job.status]));
    const reachedTerminalState = jobs.some((job) => {
      const previousStatus = previousJobStatuses.current.get(job.job_id);
      return previousStatus !== undefined && activeJobStatuses.has(previousStatus) && !activeJobStatuses.has(job.status);
    });
    previousJobStatuses.current = currentStatuses;
    if (reachedTerminalState) void refresh();
  }, [jobs, refresh]);
  useEffect(() => {
    adminMediaApi.schemes()
      .then((items) => {
        setSchemes(items);
        setSchemeError(null);
        const first = items.find((item) => item.enabled && !item.archived && item.availability === "available");
        if (first) {
          setBulkProfileId(first.scheme_id);
          setStartDefaultSchemeId((current) => current || first.scheme_id);
          setReplacementProfileId((current) => current || first.scheme_id);
          setPending((current) => current.map((item) => item.profileId ? item : { ...item, profileId: first.scheme_id }));
        }
      })
      .catch((cause) => {
        setSchemes([]);
        setSchemeError(cause instanceof Error ? cause.message : "转录方案加载失败");
      });
  }, []);
  useEffect(() => {
    adminMediaApi.categories()
      .then((items) => {
        const active = items.filter((item) => item.is_active);
        setCategories(active);
        setTargetCategoryId((current) => active.some((item) => item.id === current) ? current : active.find((item) => item.id === "cat-05")?.id || active[0]?.id || "");
      })
      .catch((cause) => setUploadError(cause instanceof Error ? cause.message : "目录加载失败"));
  }, []);
  useEffect(() => {
    if (uploadError && typeof errorAlertRef.current?.scrollIntoView === "function") {
      errorAlertRef.current.scrollIntoView({ block: "nearest" });
    }
  }, [uploadError]);

  const enabledSchemes = schemes.filter((item) => item.enabled && !item.archived && item.availability === "available");
  const editingItem = pending.find((item) => item.id === editingId);

  function addVideos(files: FileList | File[]) {
    const next = Array.from(files)
      .filter((file) => file.type === "video/mp4" || file.name.toLowerCase().endsWith(".mp4"))
      .map((file) => pendingFromFile(file, bulkProfileId || enabledSchemes[0]?.scheme_id || ""));
    if (next.length) setPending((current) => [...current, ...next]);
  }

  function updatePending(id: string, change: Partial<PendingVideo>) {
    setPending((current) => current.map((item) => item.id === id ? { ...item, ...change } : item));
    setConflictReview(null);
  }

  function chooseMode(nextMode: UploadMode) {
    setMode(nextMode);
    setPending((current) => current.map((item) => ({
      ...item,
      profileId: nextMode === "automatic" ? (item.profileId || bulkProfileId) : item.profileId,
      state: item.state === "succeeded" ? item.state : "waiting",
      error: null,
    })));
    setStep(3);
  }

  async function attachTranscript(id: string, file: File | null) {
    if (!file) return;
    try {
      const text = await file.text();
      updatePending(id, { transcriptFile: file, transcriptText: text, state: "waiting", error: null });
    } catch {
      updatePending(id, { transcriptFile: file, transcriptText: null, state: "failed", error: "无法读取 Markdown 文件" });
    }
  }

  function validateItem(item: PendingVideo) {
    if (!item.title.trim()) return "请填写视频标题";
    if (mode === "automatic") {
      const scheme = schemes.find((entry) => entry.scheme_id === item.profileId);
      if (!scheme || !scheme.enabled || scheme.archived || scheme.availability !== "available") return "请选择当前可用的转录方案";
    } else {
      if (!item.transcriptFile || item.transcriptText === null) return "请绑定 Markdown 转写文件";
      if (!item.transcriptFile.name.toLowerCase().endsWith(".md")) return "转写文件必须是 .md";
      if (!/说话[人⼈]\s+\d+\s+\d{1,2}:\d{2}/.test(item.transcriptText)) return "Markdown 缺少“说话人 HH:MM:SS”格式标记";
    }
    return null;
  }

  const readyItems = pending.filter((item) => item.state !== "succeeded" && item.state !== "skipped");
  const canSubmit = Boolean(mode && readyItems.length && readyItems.every((item) => !validateItem(item)) && !submitting);
  const mediaFilterOptions = [
    ["all", "全部任务"],
    ["processing", "处理中"],
    ["review", "待审核"],
    ["publishing", "发布处理中"],
    ["failed", "失败"],
  ] as const;
  const matchesMediaFilter = (asset: MediaAsset, filter: MediaFilter) => {
    if (filter === "all") return true;
    const job = jobsByMediaId.get(asset.media_id);
    if (filter === "processing") return job?.status === "pending" || job?.status === "running";
    if (filter === "review") return asset.review_status === "awaiting_review";
    if (filter === "publishing") return asset.publication_status === "publishing";
    return job?.status === "failed" || asset.status === "failed" || asset.publication_status === "publication_failed" || asset.publication_index_status === "failed";
  };
  const transcriptionTaskAssets = mediaAssets.filter((asset) =>
    Boolean(asset.publication_request_status)
    || Boolean(asset.transcription_job_id)
    || jobsByMediaId.has(asset.media_id)
    || asset.status === "failed"
    || asset.available_actions.includes("finalize_failed_cleanup"),
  );
  const visibleMediaAssets = transcriptionTaskAssets.filter((asset) => {
    const query = mediaQuery.trim().toLocaleLowerCase("zh-CN");
    return matchesMediaFilter(asset, mediaFilter) && (!query || `${asset.title} ${asset.original_filename}`.toLocaleLowerCase("zh-CN").includes(query));
  });
  const mediaPageCount = Math.max(1, Math.ceil(visibleMediaAssets.length / mediaPageSize));
  const pagedMediaAssets = visibleMediaAssets.slice(mediaPage * mediaPageSize, (mediaPage + 1) * mediaPageSize);
  const pageIds = pagedMediaAssets.map((asset) => asset.media_id);
  const allPageSelected = pageIds.length > 0 && pageIds.every((id) => selectedMediaIds.includes(id));
  const selectedAssets = selectedMediaIds
    .map((id) => mediaAssets.find((asset) => asset.media_id === id))
    .filter((asset): asset is MediaAsset => Boolean(asset));
  const startableSelectedAssets = selectedAssets.filter((asset) =>
    asset.available_actions.includes("start_transcription"),
  );
  const retranscribableSelectedAssets = selectedAssets.filter((asset) =>
    asset.available_actions.includes("re_transcribe"),
  );
  const startDialogAssets = startDialogMediaIds
    .map((id) => mediaAssets.find((asset) => asset.media_id === id))
    .filter((asset): asset is MediaAsset => Boolean(asset));
  const selectedJobs = selectedMediaIds.map((id) => jobsByMediaId.get(id)).filter((job): job is TranscriptionJob => Boolean(job));
  const retryableSelectedAssets = selectedAssets.filter((asset) => asset.available_actions.includes("retry_transcription"));
  const cleanableSelectedAssets = selectedAssets.filter((asset) =>
    asset.available_actions.includes("delete_failed")
    || asset.available_actions.includes("finalize_failed_cleanup"),
  );
  const batchCleanupTargets = batchCleanupTargetIds
    .map((id) => mediaAssets.find((asset) => asset.media_id === id))
    .filter((asset): asset is MediaAsset => Boolean(asset));
  const batchCleanupHasStaleExternalCache = batchCleanupTargets.some(
    (asset) => asset.available_actions.includes("finalize_failed_cleanup"),
  );
  const staleExternalCleanupTarget = deleteTarget?.available_actions.includes(
    "finalize_failed_cleanup",
  ) === true;
  const cancellableSelectedJobs = selectedJobs.filter((job) => {
    const asset = mediaAssets.find((item) => item.media_id === job.media_id);
    return asset?.available_actions.includes("cancel_transcription") === true;
  });
  const showBatchToast = (title: string, succeeded: number, failures: string[]) => {
    const message = `${title}：成功 ${succeeded} 项${failures.length ? `，失败 ${failures.length} 项` : ""}`;
    const options = failures.length ? { description: failures.join("；") } : undefined;
    if (failures.length) toast.error(message, options);
    else toast.success(message);
  };
  const runBatchRetry = async () => {
    const mediaIds = retryableSelectedAssets.map((asset) => asset.media_id);
    if (!mediaIds.length) return;
    setBatchActionBusy(true);
    setBatchMenuOpen(false);
    try {
      const result = await adminMediaApi.bulkRetryJobs(mediaIds, createRequestId());
      showBatchToast("批量重试", result.succeeded, result.items.filter((item) => item.status === "failed").map((item) => item.message || "重试失败"));
      await refreshMediaState();
    } catch (cause) {
      setUploadError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBatchActionBusy(false);
    }
  };
  const openStartDialog = (assets: MediaAsset[]) => {
    const ids = assets.map((asset) => asset.media_id);
    setStartDialogMediaIds(ids);
    setStartDefaultSchemeId((current) => current || enabledSchemes[0]?.scheme_id || "");
    setStartSchemeOverrides({});
    setBatchMenuOpen(false);
  };
  const closeStartDialog = () => {
    if (startBusy) return;
    setStartDialogMediaIds([]);
    setStartSchemeOverrides({});
    const params = new URLSearchParams(window.location.search);
    if (params.get("action") === "start-transcription") params.delete("action");
    const query = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
  };
  const startSelectedTranscriptions = async () => {
    if (!startDialogAssets.length || !startDefaultSchemeId || startBusy) return;
    setStartBusy(true);
    const failures: string[] = [];
    let succeeded = 0;
    for (const asset of startDialogAssets) {
      const schemeId = startSchemeOverrides[asset.media_id] || startDefaultSchemeId;
      try {
        await adminMediaApi.startTranscription(asset.media_id, schemeId, createRequestId());
        succeeded += 1;
      } catch (cause) {
        failures.push(`${asset.title}：${cause instanceof Error ? cause.message : "启动失败"}`);
      }
    }
    showBatchToast("批量转录", succeeded, failures);
    setStartBusy(false);
    if (!failures.length) {
      setStartDialogMediaIds([]);
      setStartSchemeOverrides({});
    }
    await refreshMediaState();
  };
  const reTranscribeDialogAssets = reTranscribeMediaIds
    .map((id) => mediaAssets.find((asset) => asset.media_id === id))
    .filter((asset): asset is MediaAsset => Boolean(asset));
  const retranscribeSameSchemeCount = reTranscribeDialogAssets.filter((asset) => {
    const selected = reTranscribeOverrides[asset.media_id] || "";
    return Boolean(asset.transcription_scheme_id && selected && selected === asset.transcription_scheme_id);
  }).length;
  const openReTranscribeDialog = (assets: MediaAsset[]) => {
    const availableSchemeIds = new Set(enabledSchemes.map((scheme) => scheme.scheme_id));
    setReTranscribeMediaIds(assets.map((asset) => asset.media_id));
    setReTranscribeOverrides(Object.fromEntries(assets.map((asset) => {
      const original = asset.transcription_scheme_id || "";
      return [asset.media_id, original && availableSchemeIds.has(original) ? original : ""];
    })));
    setBatchMenuOpen(false);
  };
  const closeReTranscribeDialog = () => {
    if (reTranscribeBusy) return;
    setReTranscribeMediaIds([]);
    setReTranscribeOverrides({});
  };
  const startReTranscriptions = async () => {
    if (!reTranscribeDialogAssets.length || reTranscribeBusy) return;
    const missing = reTranscribeDialogAssets.find((asset) => !reTranscribeOverrides[asset.media_id]);
    if (missing) {
      setUploadError(`请为“${missing.title}”选择转录方案`);
      return;
    }
    setReTranscribeBusy(true);
    const failures: string[] = [];
    let succeeded = 0;
    for (const asset of reTranscribeDialogAssets) {
      try {
        await adminMediaApi.startTranscription(asset.media_id, reTranscribeOverrides[asset.media_id], createRequestId());
        succeeded += 1;
      } catch (cause) {
        failures.push(`${asset.title}：${cause instanceof Error ? cause.message : "重新转录失败"}`);
      }
    }
    showBatchToast("批量重新转录", succeeded, failures);
    setReTranscribeBusy(false);
    if (!failures.length) {
      setReTranscribeMediaIds([]);
      setReTranscribeOverrides({});
    }
    await refreshMediaState();
  };
  const runBatchCancel = async () => { for (const job of cancellableSelectedJobs) await cancelJob(job); setSelectedMediaIds([]); setBatchMenuOpen(false); };
  const runBatchCleanup = async () => {
    if (!batchCleanupTargetIds.length) return;
    setBatchActionBusy(true);
    try {
      const result = await adminMediaApi.bulkDeleteFailedAssets(batchCleanupTargetIds);
      for (const item of result.items) {
        if (item.status === "succeeded" && item.cleanup_mode === "deleted") removeAsset(item.media_id);
      }
      showBatchToast("批量清理", result.succeeded, result.items.filter((item) => item.status === "failed").map((item) => item.message || "清理失败"));
      setBatchCleanupTargetIds([]);
      await refreshMediaState();
    } catch (cause) {
      setUploadError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBatchActionBusy(false);
    }
  };
  useEffect(() => { setMediaPage(0); setSelectedMediaIds([]); }, [mediaFilter, mediaPageSize, mediaQuery]);
  useEffect(() => { if (mediaPage >= mediaPageCount) setMediaPage(Math.max(0, mediaPageCount - 1)); }, [mediaPage, mediaPageCount]);
  const filterCounts = mediaFilterOptions.reduce<Record<MediaFilter, number>>((counts, [value]) => {
    counts[value] = transcriptionTaskAssets.filter((asset) => matchesMediaFilter(asset, value)).length;
    return counts;
  }, { all: 0, processing: 0, review: 0, publishing: 0, failed: 0 });
  const selectedAsset = selectedMediaId ? mediaAssets.find((asset) => asset.media_id === selectedMediaId) ?? null : null;
  const replaceSourceAsset = replaceSourceMediaId ? mediaAssets.find((asset) => asset.media_id === replaceSourceMediaId) ?? null : null;
  const refreshMediaState = useCallback(async () => {
    await Promise.all([refresh(), refreshJobs()]);
    setLastLoadedAt(Date.now());
  }, [refresh, refreshJobs]);

  const openWorkbench = (mediaId: string, action: "edit-current" | null = null) => {
    setSelectedMediaId(mediaId);
    setWorkbenchAction(action);
    const params = new URLSearchParams(window.location.search);
    params.set("media_id", mediaId);
    params.set("workbench", "1");
    if (action) params.set("action", action);
    else params.delete("action");
    params.delete("version_id");
    window.history.replaceState({}, "", `${window.location.pathname}?${params}`);
  };

  const closeWorkbench = () => {
    setSelectedMediaId(null);
    const params = new URLSearchParams(window.location.search);
    params.delete("media_id");
    params.delete("workbench");
    params.delete("action");
    params.delete("version_id");
    const query = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
  };

  useEffect(() => {
    if (!loading && selectedMediaId && !mediaAssets.some((asset) => asset.media_id === selectedMediaId)) closeWorkbench();
  }, [loading, mediaAssets, selectedMediaId]);

  const closeReplacement = () => {
    if (replacementBusy) return;
    setReplaceSourceMediaId(null);
    setReplacementFile(null);
    setReplacementError(null);
    setReplacementProgress(0);
    const params = new URLSearchParams(window.location.search);
    params.delete("media_id");
    params.delete("action");
    const query = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
  };

  useEffect(() => {
    if (!loading && replaceSourceMediaId && !replaceSourceAsset) closeReplacement();
  }, [loading, replaceSourceAsset, replaceSourceMediaId]);

  async function submitReplacement() {
    if (!replaceSourceAsset || !replacementFile || !replacementProfileId) return;
    try {
      const current = await adminMediaApi.schemes();
      setSchemes(current);
      if (!current.some((item) => item.scheme_id === replacementProfileId && item.enabled && item.availability === "available")) {
        setReplacementError("所选转录方案已不可用，请重新选择");
        return;
      }
    } catch (caught: any) {
      setReplacementError(caught?.message || "无法确认转录方案状态");
      return;
    }
    setReplacementBusy(true);
    setReplacementError(null);
    setReplacementProgress(0);
    try {
      const uploaded = await adminMediaApi.uploadReplacement(
        replacementFile,
        replaceSourceAsset.title,
        replacementProfileId,
        createRequestId(),
        replaceSourceAsset.media_id,
        {
          onProgress: ({ ratio }) => setReplacementProgress(ratio),
          onUploaded: () => setReplacementProgress(1),
        },
      );
      if (uploaded.transcription_job_id) {
        replaceJob(await adminMediaApi.getJob(uploaded.transcription_job_id));
      }
      setReplacementBusy(false);
      closeReplacement();
      await refreshMediaState();
    } catch (caught: any) {
      setReplacementError(caught?.message || String(caught));
      setReplacementBusy(false);
    }
  }

  async function deleteFailedMedia(asset: MediaAsset) {
    setDeletingMediaId(asset.media_id);
    try {
      const result = await adminMediaApi.deleteFailedAsset(asset.media_id);
      if (result.cleanup_mode === "deleted") removeAsset(asset.media_id);
      setDeleteTarget(null);
      toast.success(asset.available_actions.includes("finalize_failed_cleanup")
        ? "遗留缓存已清理"
        : result.cleanup_mode === "reset" ? "失败任务已清理，可重新入队" : "失败任务已删除");
      if (result.cleanup_mode === "reset") await refreshMediaState();
    } catch (e: any) {
      setUploadError(e?.message || String(e));
    } finally {
      setDeletingMediaId(null);
    }
  }

  async function archiveMedia(asset: MediaAsset) { setDeletingMediaId(asset.media_id); try { await adminMediaApi.archiveAsset(asset.media_id); removeAsset(asset.media_id); setArchiveTarget(null); setArchiveAcknowledged(false); await refreshMediaState(); } catch (e: any) { setUploadError(e?.message || String(e)); } finally { setDeletingMediaId(null); } }
  async function returnToReview(asset: MediaAsset) { if (!asset.latest_version_id) return; setDeletingMediaId(asset.media_id); try { await adminMediaApi.returnToReview(asset.latest_version_id); toast.success("已退回审核"); await refreshMediaState(); } catch (e: any) { setUploadError(e?.message || String(e)); } finally { setDeletingMediaId(null); } }
  async function moveMediaAsset() {
    if (!moveTarget?.catalog_item_id || !moveTarget.current_version_id || !moveCategoryId) return;
    setMoveBusy(true); setMoveError(null);
    try {
      await adminMediaApi.moveAsset(moveTarget.catalog_item_id, moveCategoryId, moveTarget.current_version_id);
      setMoveTarget(null); setMoveCategoryId("");
      await refresh();
    } catch (cause) {
      setMoveError(cause instanceof Error ? cause.message : "调整目录失败");
    } finally { setMoveBusy(false); }
  }

  async function uploadOne(item: PendingVideo) {
    const callbacks = {
      onProgress: ({ ratio }: { ratio: number }) => updatePending(item.id, {
        state: "uploading",
        transferRatio: ratio,
        error: null,
      }),
      onUploaded: () => updatePending(item.id, {
        state: "preparing",
        transferRatio: 1,
        error: null,
      }),
    };
    updatePending(item.id, { state: "uploading", transferRatio: 0, error: null });
    try {
      if (mode === "automatic") {
        const uploaded = await adminMediaApi.uploadAutomatic(item.file, item.title.trim(), item.profileId, item.requestId, callbacks, {
          categoryId: targetCategoryId,
          originalFilename: item.originalFilename,
          replacementSourceMediaId: item.replacementSourceMediaId || undefined,
        });
        if (uploaded.transcription_job_id) replaceJob(await adminMediaApi.getJob(uploaded.transcription_job_id));
      } else {
        const transcript = new File(
          [item.transcriptText!],
          item.transcriptFile!.name,
          { type: "text/markdown" },
        );
        await adminMediaApi.uploadManual(item.file, transcript, item.title.trim(), callbacks, {
          categoryId: targetCategoryId,
          originalFilename: item.originalFilename,
        });
      }
      updatePending(item.id, { state: "succeeded", transferRatio: 1, error: null });
    } catch (e: any) {
        const mediaHints: Record<string, string> = {
          media_audio_empty: "视频没有可用音频内容，请选择包含声音的文件。",
          media_audio_preparation_failed: "视频音频无法解码，请确认包含音轨，或重新导出为 H.264 + AAC MP4。",
          media_audio_invalid_output: "音频转换结果无效，请重新导出视频后重试。",
          media_audio_preparation_timeout: "音频准备超时，请压缩视频或重新导出后重试。",
          media_audio_source_missing: "视频文件无法读取，请重新上传。",
          media_storage_unavailable: "服务器暂时无法准备视频音频，请稍后重试。",
        };
        const code = typeof e?.code === "string" ? e.code : null;
        updatePending(item.id, { state: "failed", error: mediaHints[code || ""] || e?.message || String(e) });
    }
  }

  async function submitBatch() {
    if (!canSubmit) return;
    if (!targetCategoryId) {
      setUploadError("请选择发布后的归档目录");
      return;
    }
    if (!conflictReview) {
      try {
        const preflight = await adminMediaApi.preflightUpload({
          category_id: targetCategoryId,
          items: readyItems.map((item) => ({
            client_id: item.id,
            title: item.title.trim(),
            original_filename: item.originalFilename,
          })),
        });
        const conflicts = preflight.entries.filter((entry) => entry.status !== "ready");
        if (conflicts.length > 0) {
          setConflictReview(conflicts);
          setConflictChoices(Object.fromEntries(conflicts.map((entry) => [entry.client_id, {
            strategy: "skip" as const,
            title: entry.suggested_title || pending.find((item) => item.id === entry.client_id)?.title || "",
            originalFilename: entry.suggested_filename || pending.find((item) => item.id === entry.client_id)?.originalFilename || "",
          }])));
          setUploadError(null);
          return;
        }
      } catch (caught: any) {
        setUploadError(caught?.message || "无法检查同名资料");
        return;
      }
    } else {
      setPending((current) => current.map((item) => {
        const entry = conflictReview.find((candidate) => candidate.client_id === item.id);
        if (!entry) return item;
        const choice = conflictChoices[item.id];
        if (!choice || choice.strategy === "skip") {
          return { ...item, state: "skipped", error: null };
        }
        if (choice.strategy === "rename") {
          return { ...item, title: choice.title.trim(), originalFilename: choice.originalFilename.trim(), requestId: createRequestId(), replacementSourceMediaId: null };
        }
        const conflict = entry.conflicts[0];
        return { ...item, title: conflict.title, replacementSourceMediaId: conflict.media_id, requestId: createRequestId() };
      }));
      const resolved = readyItems.flatMap((item) => {
        const entry = conflictReview.find((candidate) => candidate.client_id === item.id);
        if (!entry) return [item];
        const choice = conflictChoices[item.id];
        if (!choice || choice.strategy === "skip") return [];
        if (choice.strategy === "rename") return [{ ...item, title: choice.title.trim(), originalFilename: choice.originalFilename.trim(), requestId: createRequestId(), replacementSourceMediaId: null }];
        const conflict = entry.conflicts[0];
        return [{ ...item, title: conflict.title, replacementSourceMediaId: conflict.media_id, requestId: createRequestId() }];
      });
      setConflictReview(null);
      if (resolved.length === 0) return;
      await performUpload(resolved);
      return;
    }
    await performUpload(readyItems);
  }

  async function performUpload(queue: PendingVideo[]) {
    if (mode === "automatic") {
      try {
        const current = await adminMediaApi.schemes();
        setSchemes(current);
        const available = new Set(current.filter((item) => item.enabled && item.availability === "available").map((item) => item.scheme_id));
        const unavailable = queue.filter((item) => !available.has(item.profileId));
        if (unavailable.length > 0) {
          setPending((items) => items.map((item) => unavailable.some((entry) => entry.id === item.id)
            ? { ...item, error: "所选转录方案已不可用，请重新选择" }
            : item));
          setUploadError("部分视频所选方案已不可用，提交已停止");
          return;
        }
      } catch (caught: any) {
        setUploadError(caught?.message || "无法确认转录方案状态");
        return;
      }
    }
    setSubmitting(true);
    setUploadError(null);
    const uploadQueue = [...queue];
    setActiveBatchIds(uploadQueue.map((item) => item.id));
    let cursor = 0;
    const worker = async () => {
      while (cursor < uploadQueue.length) {
        const item = uploadQueue[cursor++];
        await uploadOne(item);
      }
    };
    await Promise.all(Array.from({ length: Math.min(2, uploadQueue.length) }, worker));
    setSubmitting(false);
    await Promise.all([refresh(), refreshJobs()]);
  }

  async function cancelJob(job: TranscriptionJob) {
    try {
      replaceJob(await adminMediaApi.cancelJob(job.job_id));
      await refresh();
    } catch (e: any) {
      setUploadError(e?.message || String(e));
    }
  }

  async function retryMedia(asset: MediaAsset) {
    let requestKey = retryIdempotencyKeys.current.get(asset.media_id);
    if (!requestKey) {
      requestKey = createRequestId();
      retryIdempotencyKeys.current.set(asset.media_id, requestKey);
    }
    try {
      replaceJob(await adminMediaApi.retryJob(asset.media_id, requestKey));
      retryIdempotencyKeys.current.delete(asset.media_id);
      await refresh();
    } catch (e: any) {
      setUploadError(e?.message || String(e));
    }
  }

  const activeBatchItems = activeBatchIds
    .map((id) => pending.find((item) => item.id === id))
    .filter((item): item is PendingVideo => Boolean(item));
  const batchTotalBytes = activeBatchItems.reduce((total, item) => total + item.file.size, 0);
  const batchTransferredBytes = activeBatchItems.reduce(
    (total, item) => total + item.file.size * item.transferRatio,
    0,
  );
  const batchTransferProgress = batchTotalBytes > 0
    ? Math.min(100, Math.round((batchTransferredBytes / batchTotalBytes) * 100))
    : 0;
  const batchSettledCount = activeBatchItems.filter((item) => item.state === "succeeded" || item.state === "skipped" || item.state === "failed").length;
  const batchUploadingCount = activeBatchItems.filter((item) => item.state === "uploading").length;
  const batchPreparingCount = activeBatchItems.filter((item) => item.state === "preparing").length;
  const settledUploadStates = new Set<UploadState>(["succeeded", "skipped"]);
  const allUploadItemsSettled = pending.length > 0 && pending.every((item) => settledUploadStates.has(item.state));
  const hasUploadDraft = submitting || pending.some((item) => !settledUploadStates.has(item.state));
  const resetUploadDraft = () => { setStep(1); setMode(null); setPending([]); setEditingId(null); setSubmitting(false); setActiveBatchIds([]); setUploadError(null); setConflictReview(null); setConflictChoices({}); };
  const requestDiscardUpload = (mode: "close" | "cancel") => {
    setDiscardPromptMode(mode);
    setDiscardPromptOpen(true);
  };
  const confirmDiscardUpload = () => {
    resetUploadDraft();
    setDiscardPromptOpen(false);
    setUploadDialogOpen(false);
  };
  const requestUploadDialogClose = (open: boolean) => {
    if (open) { setUploadDialogOpen(true); return; }
    if (submitting) { setUploadDialogOpen(false); return; }
    if (allUploadItemsSettled) { resetUploadDraft(); setUploadDialogOpen(false); return; }
    if (hasUploadDraft) { requestDiscardUpload("close"); return; }
    setUploadDialogOpen(false);
  };

  return (
    <section className="space-y-6" aria-labelledby="admin-media-title">
      {schemeError && <Alert variant="destructive" role="alert"><AlertTitle>转录方案加载失败</AlertTitle><AlertDescription>{schemeError}</AlertDescription></Alert>}

      <Dialog open={uploadDialogOpen} onOpenChange={requestUploadDialogClose}>
      <DialogContent className="flex max-h-[min(90vh,52rem)] max-w-5xl flex-col gap-0 overflow-hidden p-0">
        <div className="shrink-0 border-b border-border px-4 py-4 sm:px-6 sm:py-5">
          <div className="flex items-start justify-between gap-4 pr-8">
            <div className="min-w-0">
              <DialogTitle className="text-ui-lg sm:text-ui-xl">上传视频与转写</DialogTitle>
              <DialogDescription className="mt-1">自动转录成功不代表已经审核、发布或进入索引。</DialogDescription>
            </div>
            {allUploadItemsSettled && <Badge variant="success" className="shrink-0"><CheckCircle2 className="size-3.5" />本批次已完成</Badge>}
          </div>
          <ol className="mt-3 grid grid-cols-3 gap-1.5 sm:gap-2" aria-label="上传步骤">
            {["上传视频", "转写方式", "配置并提交"].map((label, index) => (
              <li key={label} className={`min-w-0 rounded-ui-md border px-2 py-2 text-ui-xs sm:px-3 sm:py-2.5 sm:text-ui-sm ${step === index + 1 ? "border-primary bg-primary/10 font-medium text-primary" : "border-border text-muted-foreground"}`}>
                {index + 1}. {label}
              </li>
            ))}
          </ol>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto" data-testid="media-upload-scroll-region">
        <div className="space-y-4 px-4 py-4 sm:space-y-5 sm:px-6 sm:py-5">
          {step === 1 && (
            <>
              <div
                className={`rounded-ui-xl border-2 border-dashed p-6 text-center transition-colors sm:p-8 ${dragging ? "border-primary bg-primary/10" : "border-border bg-surface-muted/40"}`}
                onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
                onDragOver={(event) => event.preventDefault()}
                onDragLeave={() => setDragging(false)}
                onDrop={(event) => { event.preventDefault(); setDragging(false); addVideos(event.dataTransfer.files); }}
              >
                <p className="font-medium">拖放 MP4 视频到这里</p>
                <p className="mt-1 text-ui-xs text-muted-foreground">支持一次选择多个文件；此步骤只在浏览器中暂存，不会立即上传。</p>
                <label className="mt-4 inline-flex h-control-md cursor-pointer items-center rounded-ui-md bg-primary px-4 text-ui-sm font-medium text-primary-foreground">
                  选择视频文件
                  <input aria-label="选择视频文件" className="sr-only" type="file" accept=".mp4,video/mp4" multiple onChange={(event) => event.target.files && addVideos(event.target.files)} />
                </label>
              </div>
              {pending.length > 0 && (
                <div className="max-h-64 space-y-2 overflow-y-auto pr-1" aria-label="待上传视频">
                  {pending.map((item) => (
                    <div key={item.id} className="flex items-center gap-3 rounded-ui-lg border border-border p-3">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-ui-sm font-medium">{item.file.name}</p>
                        <p className="text-ui-xs text-muted-foreground">{formatBytes(item.file.size)}</p>
                      </div>
                      <Button variant="ghost" size="sm" onClick={() => setPending((current) => current.filter((entry) => entry.id !== item.id))}>移除</Button>
                    </div>
                  ))}
                </div>
              )}
                <div className="flex justify-end"><Button disabled={!pending.length} onClick={() => setStep(2)}>下一步：选择转写方式</Button></div>
            </>
          )}

          {step === 2 && (
            <>
              <div className="grid gap-4 md:grid-cols-2">
                <button type="button" className="rounded-ui-xl border border-border p-5 text-left hover:border-primary hover:bg-primary/5" onClick={() => chooseMode("automatic")}>
                  <span className="font-semibold">自动转录</span>
                  <span className="mt-2 block text-ui-sm text-muted-foreground">使用管理员维护的有序转录方案生成候选草稿。</span>
                </button>
                <button type="button" className="rounded-ui-xl border border-border p-5 text-left hover:border-primary hover:bg-primary/5" onClick={() => chooseMode("manual")}>
                  <span className="font-semibold">人工转写</span>
                  <span className="mt-2 block text-ui-sm text-muted-foreground">为每个视频绑定、查看并编辑已准备好的 Markdown 转写文件。</span>
                </button>
              </div>
              <Button variant="outline" onClick={() => setStep(1)}>返回上传视频</Button>
            </>
          )}

          {step === 3 && mode && (
            <>
              <CategoryTreePicker
                categories={categories}
                value={targetCategoryId}
                onChange={(categoryId) => { setTargetCategoryId(categoryId); setConflictReview(null); }}
                label="发布后的归档目录"
              />
              <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" onClick={() => setPending((current) => current.map((item) => ({ ...item, selected: true })))}>全选</Button>
                  <Button variant="outline" onClick={() => setPending((current) => current.map((item) => ({ ...item, selected: false })))}>取消全选</Button>
                </div>
                {mode === "automatic" && (
                  <div className="grid min-w-0 gap-2 sm:grid-cols-[minmax(16rem,20rem)_auto] sm:items-end">
                    <label className="min-w-0 text-ui-sm font-medium">批量转录方案
                      <Select aria-label="批量转录方案" value={bulkProfileId} onChange={(event) => setBulkProfileId(event.target.value)} className="mt-1">
                        <option value="">请选择转录方案</option>
                        {schemes.map((scheme) => <option key={scheme.scheme_id} value={scheme.scheme_id} disabled={!scheme.enabled || scheme.archived || scheme.availability !== "available"}>{scheme.name}{scheme.availability !== "available" ? "（不可用）" : ""}</option>)}
                      </Select>
                    </label>
                    <Button variant="outline" className="w-full sm:w-auto" onClick={() => setPending((current) => current.map((item) => item.selected ? { ...item, profileId: bulkProfileId, requestId: createRequestId(), state: "waiting", error: null } : item))}>应用到已选择视频</Button>
                  </div>
                )}
              </div>

              <div className="max-h-[32rem] space-y-3 overflow-y-auto pr-1" aria-label="上传配置列表">
                {pending.map((item) => {
                  const validationError = validateItem(item);
                  const scheme = schemes.find((entry) => entry.scheme_id === item.profileId);
                  const itemSettled = settledUploadStates.has(item.state);
                  return (
                    <div key={item.id} className="rounded-ui-xl border border-border p-4">
                      <div className="flex gap-3">
                        {!itemSettled && <Checkbox aria-label={`选择 ${item.file.name}`} checked={item.selected} onChange={(event) => updatePending(item.id, { selected: event.target.checked })} />}
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-medium">{item.file.name}</p>
                          <p className="text-ui-xs text-muted-foreground">{formatBytes(item.file.size)}</p>
                        </div>
                        <StatusBadge value={item.state} meta={{ waiting: { label: "待提交", variant: "secondary" }, uploading: { label: "上传中", variant: "warning" }, preparing: { label: "服务端处理中", variant: "info" }, succeeded: { label: "已提交", variant: "success" }, skipped: { label: "已跳过", variant: "secondary" }, failed: { label: "提交失败", variant: "destructive" } }} />
                      </div>
                      {(item.state === "uploading" || item.state === "preparing") && (
                        <div className="mt-3 space-y-1.5" role="status" aria-live="polite" aria-atomic="true">
                          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-ui-xs">
                            <span className="font-medium text-foreground">
                              {item.state === "uploading"
                                ? `正在上传 · ${Math.round(item.transferRatio * 100)}%`
                                : mode === "automatic"
                                  ? "文件已上传，正在准备音轨并创建转录任务"
                                  : "文件已上传，正在创建媒体记录"}
                            </span>
                            {item.state === "uploading" && (
                              <span className="tabular-nums text-muted-foreground">
                                {formatBytes(Math.round(item.file.size * item.transferRatio))} / {formatBytes(item.file.size)}
                              </span>
                            )}
                          </div>
                          <Progress
                            label={`${item.file.name} 上传进度`}
                            value={item.state === "uploading" ? item.transferRatio * 100 : null}
                          />
                        </div>
                      )}
                      {itemSettled ? (
                        <dl className="mt-3 grid gap-x-5 gap-y-2 border-t border-border pt-3 text-ui-xs sm:grid-cols-3">
                          <div className="min-w-0"><dt className="text-muted-foreground">视频标题</dt><dd className="mt-0.5 truncate font-medium text-foreground" title={item.title}>{item.title}</dd></div>
                          <div className="min-w-0"><dt className="text-muted-foreground">源文件名</dt><dd className="mt-0.5 truncate font-medium text-foreground" title={item.originalFilename}>{item.originalFilename}</dd></div>
                          <div className="min-w-0"><dt className="text-muted-foreground">{mode === "automatic" ? "转录方案" : "人工转写"}</dt><dd className="mt-0.5 truncate font-medium text-foreground" title={mode === "automatic" ? scheme?.name : item.transcriptFile?.name}>{mode === "automatic" ? scheme?.name || "历史方案" : item.transcriptFile?.name || "未绑定"}</dd></div>
                        </dl>
                      ) : <><div className="mt-3 grid gap-3 lg:grid-cols-2">
                        <label className="text-ui-sm font-medium">视频标题
                          <Input aria-label={`${item.file.name} 的视频标题`} className="mt-1" value={item.title} disabled={submitting || item.state === "succeeded"} onChange={(event) => updatePending(item.id, { title: event.target.value, requestId: createRequestId(), state: "waiting", error: null })} />
                        </label>
                        <label className="text-ui-sm font-medium">源文件名
                          <Input aria-label={`${item.file.name} 的源文件名`} className="mt-1" value={item.originalFilename} disabled={submitting || item.state === "succeeded"} onChange={(event) => updatePending(item.id, { originalFilename: event.target.value, requestId: createRequestId(), state: "waiting", error: null })} />
                        </label>
                        {mode === "automatic" ? (
                          <label className="text-ui-sm font-medium">转录方案
                            <Select aria-label={`${item.file.name} 的转录方案`} className="mt-1" value={item.profileId} disabled={submitting || item.state === "succeeded"} onChange={(event) => updatePending(item.id, { profileId: event.target.value, requestId: createRequestId(), state: "waiting", error: null })}>
                              <option value="">请选择转录方案</option>
                              {schemes.map((entry) => <option key={entry.scheme_id} value={entry.scheme_id} disabled={!entry.enabled || entry.archived || entry.availability !== "available"}>{entry.name}{entry.availability !== "available" ? "（不可用）" : ""}</option>)}
                            </Select>
                            {scheme && <span className="mt-1 block text-ui-xs text-muted-foreground">{scheme.description}</span>}
                          </label>
                        ) : (
                          <div>
                            <span className="text-ui-sm font-medium">人工 Markdown</span>
                            <div className="mt-1 flex flex-wrap gap-2">
                              <label className="inline-flex h-control-md cursor-pointer items-center rounded-ui-md border border-input bg-background px-3 text-ui-sm">
                                {item.transcriptFile ? "更换转写文件" : "添加转写文件"}
                                <input aria-label={`${item.file.name} 的人工转写`} className="sr-only" type="file" accept=".md,text/markdown" disabled={submitting || item.state === "succeeded"} onChange={(event) => void attachTranscript(item.id, event.target.files?.[0] || null)} />
                              </label>
                              {item.transcriptFile && <Button variant="outline" onClick={() => { setEditingId(item.id); setUploadDialogOpen(false); }}>打开并编辑</Button>}
                            </div>
                            <p className="mt-1 text-ui-xs text-muted-foreground">{item.transcriptFile?.name || "尚未绑定 Markdown"}</p>
                          </div>
                        )}
                      </div>
                      {(item.error || validationError) && <p className="mt-2 text-ui-xs text-destructive">{item.error || validationError}</p>}</>}
                    </div>
                  );
                })}
              </div>

              {conflictReview && <div className="space-y-3 rounded-ui-lg border border-warning/50 bg-warning/10 p-4" role="status">
                <div><p className="font-semibold">发现同名资料</p><p className="mt-1 text-ui-xs text-muted-foreground">每个视频可跳过、另存为新资料，或作为唯一命中资料的新版本。提交前会再次校验。</p></div>
                <ul className="space-y-3">{conflictReview.map((entry) => {
                  const item = pending.find((candidate) => candidate.id === entry.client_id);
                  const choice = conflictChoices[entry.client_id];
                  const canUpdate = mode === "automatic" && entry.status === "conflict" && entry.conflicts.length === 1 && Boolean(entry.conflicts[0].item_id && entry.conflicts[0].version_id);
                  return <li key={entry.client_id} className="space-y-3 rounded-ui-md border border-border bg-background p-3">
                    <div><p className="break-all font-medium">{item?.title}（{item?.originalFilename}）</p>{entry.conflicts.map((conflict) => <p key={conflict.media_id} className="mt-1 text-ui-xs text-muted-foreground">已有资料：{conflict.title}（{conflict.original_filename}）{conflict.title_matches ? " · 标题同名" : ""}{conflict.filename_matches ? " · 源文件同名" : ""}</p>)}{entry.conflicts.length === 0 && <p className="mt-1 text-ui-xs text-muted-foreground">本批次中存在相同标题或源文件名，请跳过或重命名。</p>}</div>
                    <div className="grid gap-2 sm:grid-cols-2"><label className="text-ui-xs font-medium">处理方式<Select className="mt-1" value={choice?.strategy || "skip"} onChange={(event) => setConflictChoices((current) => ({ ...current, [entry.client_id]: { ...current[entry.client_id], strategy: event.target.value as MediaConflictChoice["strategy"] } }))}><option value="skip">跳过此视频</option><option value="rename">另存为新资料</option>{canUpdate && <option value="update">作为已有资料的新版本</option>}</Select></label>{choice?.strategy === "rename" && <><label className="text-ui-xs font-medium">新资料标题<Input className="mt-1" value={choice.title} onChange={(event) => setConflictChoices((current) => ({ ...current, [entry.client_id]: { ...current[entry.client_id], title: event.target.value } }))} /></label><label className="text-ui-xs font-medium">新源文件名<Input className="mt-1" value={choice.originalFilename} onChange={(event) => setConflictChoices((current) => ({ ...current, [entry.client_id]: { ...current[entry.client_id], originalFilename: event.target.value } }))} /></label></>}</div>
                  </li>;
                })}</ul>
              </div>}

              {submitting && activeBatchItems.length > 0 && (
                <div className="space-y-2 border-t border-border pt-4" role="status" aria-live="polite" aria-atomic="true">
                  <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-ui-xs">
                    <span className="font-medium text-foreground">
                      已处理 {batchSettledCount}/{activeBatchItems.length}
                      {batchUploadingCount > 0 ? ` · 正在上传 ${batchUploadingCount} 个` : ""}
                      {batchPreparingCount > 0 ? ` · 服务端处理 ${batchPreparingCount} 个` : ""}
                    </span>
                    <span className="tabular-nums text-muted-foreground">
                      文件传输 {batchTransferProgress}% · {formatBytes(batchTransferredBytes)} / {formatBytes(batchTotalBytes)}
                    </span>
                  </div>
                  <Progress label="批量文件传输进度" value={batchTransferProgress} />
                </div>
              )}

            </>
          )}
        </div>
        </div>
        {step === 3 && mode && (
          <div className="shrink-0 border-t border-border bg-popover px-4 py-4 sm:px-6" data-testid="media-upload-action-bar">
            {!allUploadItemsSettled && <p className="mb-3 text-ui-xs text-muted-foreground sm:text-right">{mode === "manual" ? "保持现有人工 Markdown 上传与索引路径。" : "每个文件使用独立幂等键；最多并发上传 2 个。"}</p>}
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <Button variant="outline" className="w-full sm:w-auto" disabled={submitting || allUploadItemsSettled} onClick={() => setStep(2)}><ArrowLeft className="size-4" />返回选择方式</Button>
                {!allUploadItemsSettled && <Button variant="destructive" className="w-full sm:w-auto" disabled={submitting} onClick={() => requestDiscardUpload("cancel")}><Trash2 className="size-4" />放弃本次上传</Button>}
              </div>
              {allUploadItemsSettled ? (
                <Button className="w-full sm:w-auto" onClick={() => requestUploadDialogClose(false)}><CheckCircle2 className="size-4" />完成并关闭</Button>
              ) : (
                <Button className="w-full sm:w-auto" disabled={!canSubmit || !targetCategoryId} onClick={() => void submitBatch()}>{submitting ? "正在批量提交…" : conflictReview ? "按选择上传" : mode === "manual" ? "上传视频与人工转写" : "上传并创建自动转录任务"}</Button>
              )}
            </div>
          </div>
        )}
      </DialogContent>
      </Dialog>

      <Dialog open={discardPromptOpen} onOpenChange={setDiscardPromptOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{discardPromptMode === "cancel" ? "放弃本次上传？" : "暂时关闭上传流程？"}</DialogTitle>
            <DialogDescription>
              {discardPromptMode === "cancel"
                ? "已提交的任务不会被撤回；未提交的视频将从当前浏览器流程中清除。"
                : "可保留未提交的视频和填写内容供下次继续，也可放弃并清空本次上传。"}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="flex-col-reverse sm:flex-row">
            <Button variant="outline" className="w-full sm:w-auto" onClick={() => setDiscardPromptOpen(false)}>继续操作</Button>
            {discardPromptMode === "cancel" ? (
              <Button variant="destructive" className="w-full sm:w-auto" onClick={confirmDiscardUpload}>放弃并清空</Button>
            ) : (
              <>
                <Button variant="destructive" className="w-full sm:w-auto" onClick={confirmDiscardUpload}>关闭并放弃</Button>
                <Button className="w-full sm:w-auto" onClick={() => { setDiscardPromptOpen(false); setUploadDialogOpen(false); }}>关闭并保留</Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {editingItem && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" aria-labelledby="transcript-editor-title">
          <Card className="flex max-h-[90vh] w-full max-w-4xl flex-col">
            <CardHeader><CardTitle id="transcript-editor-title">编辑 {editingItem.transcriptFile?.name}</CardTitle><CardDescription>编辑只保存在本次浏览器待上传内容中。</CardDescription></CardHeader>
            <CardContent className="flex min-h-0 flex-1 flex-col gap-3">
              <textarea aria-label="Markdown 转写内容" className="min-h-80 flex-1 resize-y rounded-ui-md border border-input bg-background p-3 font-mono text-ui-sm" value={editingItem.transcriptText || ""} onChange={(event) => updatePending(editingItem.id, { transcriptText: event.target.value, state: "waiting", error: null })} />
              <div className="flex justify-end"><Button onClick={() => setEditingId(null)}>保存并关闭</Button></div>
            </CardContent>
          </Card>
        </div>
      )}

      {uploadError && <div ref={errorAlertRef}><Alert variant="destructive" role="alert"><AlertTitle>操作失败</AlertTitle><AlertDescription>{uploadError}</AlertDescription></Alert></div>}

      <section className="space-y-5" aria-labelledby="media-assets-title">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5" aria-label="媒体快捷筛选">
          {mediaFilterOptions.map(([value, label]) => { const icons = { all: <Film className="size-4" />, processing: <LoaderCircle className="size-4" />, review: <ClipboardCheck className="size-4" />, publishing: <Rocket className="size-4" />, failed: <XCircle className="size-4" /> }; return <ManagedSummaryCard key={value} label={label} value={filterCounts[value]} icon={icons[value]} tone={value === "failed" ? "destructive" : value === "review" || value === "publishing" ? "warning" : "primary"} active={mediaFilter === value} onClick={() => setMediaFilter(value)} />; })}
        </div>
        <Card className="overflow-hidden shadow-surface">
          <div className="grid gap-3 border-b border-border px-4 py-4 sm:px-5 lg:grid-cols-[minmax(13rem,1fr)_18rem_auto] lg:items-end">
            <div className="min-w-0"><h2 id="media-assets-title" className="text-ui-base font-semibold">视频资源</h2><p className="mt-1 text-ui-xs text-muted-foreground">视频由资料列表上传，在这里跟踪转录、审核、发布、专属索引和恢复操作。</p></div>
            <div className="relative min-w-0"><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" /><Input type="search" aria-label="搜索转录任务" placeholder="搜索标题或文件名…" className="h-control-md pl-9 text-ui-xs" value={mediaQuery} onChange={(event) => setMediaQuery(event.target.value)} /></div>
            <div className="flex flex-wrap items-center gap-2 lg:justify-end">
              <a className={`${buttonVariants({ variant: "outline", size: "sm" })} h-control-md`} href="/admin/asr"><Settings2 className="size-4" />转录配置</a>
              <Button size="sm" variant="outline" className="h-control-md" aria-label="刷新媒体资源" title="刷新媒体资源" disabled={loading} onClick={() => void refreshMediaState()}><RefreshCw className="size-4" aria-hidden="true" />刷新列表</Button>
              <div className="relative"><Button size="sm" variant="outline" className="h-control-md" disabled={!selectedMediaIds.length || batchActionBusy} aria-haspopup="menu" aria-expanded={batchMenuOpen} onClick={() => setBatchMenuOpen((open) => !open)}>批量操作<ChevronDown className="size-4" /></Button>{batchMenuOpen && <div role="menu" aria-label="批量操作" className="absolute right-0 top-full z-dropdown mt-1 w-48 rounded-ui-md border border-border bg-popover p-1 shadow-overlay"><button type="button" role="menuitem" className="flex w-full items-center gap-2 rounded-ui-sm px-3 py-2 text-ui-sm hover:bg-surface-muted disabled:opacity-40" title={startableSelectedAssets.length ? undefined : "所选视频均不可开始转录"} disabled={!startableSelectedAssets.length || batchActionBusy} onClick={() => openStartDialog(startableSelectedAssets)}><Rocket className="size-4" />开始转录（{startableSelectedAssets.length}）</button><button type="button" role="menuitem" className="flex w-full items-center gap-2 rounded-ui-sm px-3 py-2 text-ui-sm hover:bg-surface-muted disabled:opacity-40" title={retranscribableSelectedAssets.length ? undefined : "所选视频均不可重新转录"} disabled={!retranscribableSelectedAssets.length || batchActionBusy} onClick={() => openReTranscribeDialog(retranscribableSelectedAssets)}><RefreshCcw className="size-4" />重新转录所选（{retranscribableSelectedAssets.length}）</button><button type="button" role="menuitem" className="flex w-full items-center gap-2 rounded-ui-sm px-3 py-2 text-ui-sm hover:bg-surface-muted disabled:opacity-40" title={retryableSelectedAssets.length ? undefined : "所选视频均不可重试"} disabled={!retryableSelectedAssets.length || batchActionBusy} onClick={() => void runBatchRetry()}><RotateCcw className="size-4" />重试所选（{retryableSelectedAssets.length}）</button><button type="button" role="menuitem" className="flex w-full items-center gap-2 rounded-ui-sm px-3 py-2 text-ui-sm hover:bg-surface-muted disabled:opacity-40" title={cancellableSelectedJobs.length ? undefined : "没有正在运行的转录任务可取消"} disabled={!cancellableSelectedJobs.length || batchActionBusy} onClick={() => void runBatchCancel()}><Ban className="size-4" />取消所选（{cancellableSelectedJobs.length}）</button><button type="button" role="menuitem" className="flex w-full items-center gap-2 rounded-ui-sm px-3 py-2 text-ui-sm text-destructive hover:bg-destructive/10 disabled:opacity-40" title={cleanableSelectedAssets.length ? undefined : "所选视频均不可清理"} disabled={!cleanableSelectedAssets.length || batchActionBusy} onClick={() => { setBatchCleanupTargetIds(cleanableSelectedAssets.map((asset) => asset.media_id)); setBatchMenuOpen(false); }}><Trash2 className="size-4" />清理所选（{cleanableSelectedAssets.length}）</button></div>}</div>
              {!embedded && <Button size="sm" className="h-control-md" onClick={() => setUploadDialogOpen(true)}><Upload className="size-4" />{hasUploadDraft ? `继续上传${pending.length ? `（${pending.length}）` : ""}` : "上传视频"}</Button>}
            </div>
          </div>
        {jobsError && <Alert role="alert"><AlertTitle>任务状态暂时无法刷新</AlertTitle><AlertDescription>{jobsError}</AlertDescription></Alert>}
        {loadError ? <ErrorState title="媒体资源加载失败" description={loadError} action={<Button variant="outline" size="sm" onClick={refresh}>重新加载</Button>} />
          : loading ? <Card><LoadingState className="min-h-48" label="正在加载媒体资源…" /></Card>
          : transcriptionTaskAssets.length === 0 ? <EmptyState title="暂无转录任务" description="视频在资料列表发布后，会进入这里等待选择转录方案。" />
          : <>
            <div className="hidden grid-cols-[3rem_minmax(0,31fr)_minmax(0,42fr)_minmax(0,12fr)_minmax(0,15fr)] gap-4 border-b border-border bg-surface-muted px-5 py-3 text-ui-sm font-medium text-muted-foreground lg:grid" data-testid="media-record-header">
              <Checkbox aria-label="选择当前页视频" checked={allPageSelected} onChange={() => setSelectedMediaIds(allPageSelected ? [] : pageIds)} /><span>媒体信息</span><span>处理进度</span><span>最近提交</span><span>操作</span>
            </div>
            <ul className="divide-y divide-border" aria-label="视频处理记录">
              {pagedMediaAssets.map((asset) => {
                const job = jobsByMediaId.get(asset.media_id);
                const sameNameCount = mediaAssets.filter((item) => item.original_filename === asset.original_filename).length;
                const availableActions = new Set(asset.available_actions);
                const disabledActions = asset.disabled_actions;
                const isExternal = asset.storage_kind === "external";
                const canStart = availableActions.has("start_transcription");
                const canReTranscribe = availableActions.has("re_transcribe");
                const canCancel = availableActions.has("cancel_transcription");
                const canRetry = availableActions.has("retry_transcription");
                const canDelete = availableActions.has("delete_failed");
                const canFinalizeCleanup = availableActions.has("finalize_failed_cleanup");
                const canReplace = availableActions.has("replace_media");
                const canArchive = availableActions.has("archive_media");
                const canReturnToReview = availableActions.has("return_to_review");
                const isStaleExternalCleanup = canFinalizeCleanup;
                const hasSchemeEntry = Boolean(asset.transcription_scheme_id || job?.scheme_id);
                const schemeName = job?.scheme_name ?? asset.transcription_scheme_name;
                const schemeDeleted = Boolean(job?.scheme_deleted ?? asset.transcription_scheme_deleted);
                return <li key={asset.media_id} className="p-4 transition-colors duration-normal hover:bg-surface-muted/60 sm:p-5" data-testid="media-record-row">
                  <div className="grid grid-cols-[3rem_minmax(0,1fr)] gap-4 lg:grid-cols-[3rem_minmax(0,31fr)_minmax(0,42fr)_minmax(0,12fr)_minmax(0,15fr)] lg:items-start">
                    <Checkbox aria-label={`选择“${asset.title}”`} checked={selectedMediaIds.includes(asset.media_id)} onChange={() => setSelectedMediaIds((current) => current.includes(asset.media_id) ? current.filter((id) => id !== asset.media_id) : [...current, asset.media_id])} />
                    <div className="min-w-0">
                      <p className="truncate font-medium" title={asset.title}>{asset.title}</p>
                      <p className="mt-1 truncate font-mono text-ui-xs text-muted-foreground" title={asset.original_filename}>{asset.original_filename}</p>
                      <p className="mt-1 truncate text-ui-xs text-muted-foreground" title={categories.find((category) => category.id === asset.category_id)?.full_path}>{categories.find((category) => category.id === asset.category_id)?.full_path || "尚未选择归档目录"}</p>
                      <div className="mt-2 flex flex-wrap gap-2 text-ui-xs text-muted-foreground"><span>{formatBytes(asset.file_size)}</span>{isExternal && <Badge variant="secondary">共享目录视频 · 只读</Badge>}{sameNameCount > 1 && <Badge variant="secondary">同名记录 {sameNameCount} 条</Badge>}{asset.replacement_source_media_id && <Badge variant={asset.replacement_status === "activated" ? "success" : asset.replacement_status === "failed" ? "destructive" : "warning"}>{asset.replacement_status === "activated" ? "替换已生效" : asset.replacement_status === "failed" ? "替换候选失败" : "替换候选"}</Badge>}{asset.replacement_candidate_media_id && asset.replacement_status === "pending" && <Badge variant="warning">替换处理中</Badge>}</div>
                    </div>
                    <div className="min-w-0 space-y-2">
                      <div className="flex flex-wrap items-center gap-2"><StatusBadge value={job?.status || asset.status} meta={job ? jobStatusMeta : mediaStatusMeta} /></div>
                      {asset.error && !job && <p className="text-ui-xs text-destructive">媒体处理失败，请修复转录方案后重试。原因：{asset.error}{asset.storage_kind === "external" ? "（共享目录原文件不可删除）" : ""}</p>}
                      {job ? <JobSummary job={job} /> : <p className="text-ui-xs text-muted-foreground">{asset.transcript_origin === "manual" ? "人工转写" : "尚未创建转录任务"}</p>}
                      {hasSchemeEntry && <div className="flex flex-wrap items-center gap-1.5 text-ui-xs text-muted-foreground" data-testid="media-scheme-line"><span>转录方案：{schemeName ? <span className="font-medium text-foreground">{schemeName}</span> : "原转录配置已删除"}</span>{schemeName && schemeDeleted && <Badge variant="secondary">原转录配置已删除</Badge>}</div>}
                      <LifecycleRail asset={asset} />
                    </div>
                    <p className="text-ui-xs text-muted-foreground"><span className="sr-only">提交时间：</span>{formatAdminDate(asset.created_at)}</p>
                    <div className="flex flex-wrap gap-1.5 lg:justify-end" aria-label={`媒体操作：${asset.title}`}>
                      <IconButton label="转录" title={canStart ? "选择转录方案开始转录" : disabledActions.start_transcription || "当前不可开始转录"} tooltip={canStart ? "选择转录方案开始转录" : disabledActions.start_transcription || "当前不可开始转录"} className="border border-border max-sm:size-control-md" disabled={!canStart || deletingMediaId === asset.media_id} onClick={() => openStartDialog([asset])}><Rocket className="size-4" /></IconButton>
                      <IconButton label="重新转录" title={canReTranscribe ? "选择转录方案后重新生成转录稿" : disabledActions.re_transcribe || "当前不可重新转录"} tooltip={canReTranscribe ? "选择转录方案后重新生成转录稿" : disabledActions.re_transcribe || "当前不可重新转录"} className="border border-border max-sm:size-control-md" disabled={!canReTranscribe || deletingMediaId === asset.media_id} onClick={() => openReTranscribeDialog([asset])}><RefreshCcw className="size-4" /></IconButton>
                      <IconButton label="进入转写工作台" tooltip="进入转写工作台" className="border border-border max-sm:size-control-md" onClick={() => openWorkbench(asset.media_id)}><Film className="size-4" /></IconButton>
                      <IconButton label="调整目录" title={asset.catalog_item_id && asset.current_version_id ? "调整资料库中的归档目录" : "该视频尚未发布正式转录稿，无法调整目录"} tooltip={asset.catalog_item_id && asset.current_version_id ? "调整资料库中的归档目录" : "该视频尚未发布正式转录稿，无法调整目录"} className="border border-border max-sm:size-control-md" disabled={!asset.catalog_item_id || !asset.current_version_id || deletingMediaId === asset.media_id} onClick={() => { setMoveTarget(asset); setMoveCategoryId(""); setMoveError(null); }}><FolderInput className="size-4" /></IconButton>
                      <IconButton label="取消" title={canCancel ? "取消当前转录任务" : disabledActions.cancel_transcription || "当前状态不可取消"} tooltip={canCancel ? "取消当前转录任务" : disabledActions.cancel_transcription || "当前状态不可取消"} className="border border-border max-sm:size-control-md" disabled={!canCancel || deletingMediaId === asset.media_id} onClick={() => job && void cancelJob(job)}><Ban className="size-4" /></IconButton>
                      <IconButton label="重试" title={canRetry ? "由服务端按原转录方案重试" : disabledActions.retry_transcription || "当前状态不可重试"} tooltip={canRetry ? "由服务端按原转录方案重试" : disabledActions.retry_transcription || "当前状态不可重试"} className="border border-border max-sm:size-control-md" disabled={!canRetry || deletingMediaId === asset.media_id} onClick={() => void retryMedia(asset)}><RotateCcw className="size-4" /></IconButton>
                      <IconButton label="退回审核" title={canReturnToReview ? "将未发布转录稿退回待审核状态" : disabledActions.return_to_review || "仅审核通过且未发布的转录稿可退回审核"} tooltip={canReturnToReview ? "将未发布转录稿退回待审核状态" : disabledActions.return_to_review || "仅审核通过且未发布的转录稿可退回审核"} className="border border-border max-sm:size-control-md" disabled={!canReturnToReview || !asset.latest_version_id || deletingMediaId === asset.media_id} onClick={() => void returnToReview(asset)}><ArrowLeft className="size-4" /></IconButton>
                      <IconButton label="替换视频" title={canReplace ? "上传候选视频并创建转录任务" : disabledActions.replace_media || "当前状态不可替换"} tooltip={canReplace ? "上传候选视频并创建转录任务" : disabledActions.replace_media || "当前状态不可替换"} className="border border-border max-sm:size-control-md" disabled={!canReplace || deletingMediaId === asset.media_id} onClick={() => setReplaceSourceMediaId(asset.media_id)}><Repeat2 className="size-4" /></IconButton>
                      <IconButton label={isStaleExternalCleanup ? "完成缓存清理" : "清理失败任务"} title={isStaleExternalCleanup ? "只删除上次清理遗留的暂存缓存" : canDelete ? (isExternal ? "清理本地任务和缓存，保留共享原文件" : "删除失败媒体和本地任务历史") : disabledActions.delete_failed || "当前失败任务不可清理"} tooltip={isStaleExternalCleanup ? "只删除上次清理遗留的暂存缓存" : canDelete ? (isExternal ? "清理本地任务和缓存，保留共享原文件" : "删除失败媒体和本地任务历史") : disabledActions.delete_failed || "当前失败任务不可清理"} className={canDelete || canFinalizeCleanup ? "border border-destructive/40 text-destructive max-sm:size-control-md" : "border border-border max-sm:size-control-md"} disabled={(!canDelete && !canFinalizeCleanup) || deletingMediaId === asset.media_id} onClick={() => setDeleteTarget(asset)}><Trash2 className="size-4" /></IconButton>
                      <IconButton label="移入回收站" title={canArchive ? "移入资料回收站" : disabledActions.archive_media || "当前状态不可归档"} tooltip={canArchive ? "移入资料回收站" : disabledActions.archive_media || "当前状态不可归档"} className="border border-border max-sm:size-control-md" disabled={!canArchive || deletingMediaId === asset.media_id} onClick={() => { setArchiveTarget(asset); setArchiveAcknowledged(false); }}><Archive className="size-4" /></IconButton>
                    </div>
                  </div>
                </li>;
              })}
            </ul>
          </>}
        {visibleMediaAssets.length === 0 && transcriptionTaskAssets.length > 0 && <EmptyState title="没有符合条件的媒体" description="请切换其他快捷筛选条件。" />}
        <div className="flex flex-col gap-2 border-t border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5"><p className="text-ui-xs text-muted-foreground">当前显示 {visibleMediaAssets.length ? mediaPage * mediaPageSize + 1 : 0} - {Math.min((mediaPage + 1) * mediaPageSize, visibleMediaAssets.length)} / {visibleMediaAssets.length} 条记录{lastLoadedAt ? ` · 最近刷新 ${new Date(lastLoadedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}` : ""}。</p><div className="flex flex-wrap items-center gap-2"><label className="flex items-center gap-2 text-ui-xs text-muted-foreground">每页<Select aria-label="每页视频条数" className="h-control-sm w-20" value={String(mediaPageSize)} onChange={(event) => setMediaPageSize(Number(event.target.value))}><option value="10">10 条</option><option value="20">20 条</option><option value="50">50 条</option></Select></label><Button size="sm" variant="outline" disabled={mediaPage === 0} onClick={() => setMediaPage((value) => value - 1)}>上一页</Button><Select aria-label="跳转视频页码" className="h-control-sm w-24" value={String(mediaPage + 1)} onChange={(event) => setMediaPage(Number(event.target.value) - 1)}>{Array.from({ length: mediaPageCount }, (_, index) => <option key={index + 1} value={index + 1}>第 {index + 1} 页</option>)}</Select><Button size="sm" variant="outline" disabled={mediaPage + 1 >= mediaPageCount} onClick={() => setMediaPage((value) => value + 1)}>下一页</Button></div></div>
        </Card>
      </section>

      <Dialog open={startDialogMediaIds.length > 0} onOpenChange={(open) => { if (!open) closeStartDialog(); }}>
        <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>配置转录方案</DialogTitle>
            <DialogDescription>为所选视频设置默认方案，也可逐个覆盖后批量启动。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <label className="block text-ui-sm font-medium">默认转录方案
              <Select aria-label="默认转录方案" className="mt-1" value={startDefaultSchemeId} disabled={startBusy} onChange={(event) => setStartDefaultSchemeId(event.target.value)}>
                <option value="">请选择可用方案</option>
                {schemes.map((scheme) => <option key={scheme.scheme_id} value={scheme.scheme_id} disabled={!scheme.enabled || scheme.archived || scheme.availability !== "available"}>{scheme.name}{scheme.availability !== "available" ? "（不可用）" : ""}</option>)}
              </Select>
            </label>
            <ul className="divide-y divide-border rounded-ui-md border border-border" aria-label="受影响的视频文件">
              {startDialogAssets.map((asset) => <li key={asset.media_id} className="grid gap-3 p-3 sm:grid-cols-[minmax(0,1fr)_minmax(12rem,18rem)] sm:items-center">
                <div className="min-w-0"><p className="truncate text-ui-sm font-medium">{asset.title}</p><p className="truncate text-ui-xs text-muted-foreground">{asset.original_filename}</p></div>
                <Select aria-label={`${asset.title}的转录方案`} value={startSchemeOverrides[asset.media_id] || ""} disabled={startBusy} onChange={(event) => setStartSchemeOverrides((current) => ({ ...current, [asset.media_id]: event.target.value }))}>
                  <option value="">使用默认方案</option>
                  {schemes.map((scheme) => <option key={scheme.scheme_id} value={scheme.scheme_id} disabled={!scheme.enabled || scheme.archived || scheme.availability !== "available"}>{scheme.name}</option>)}
                </Select>
              </li>)}
            </ul>
          </div>
          <DialogFooter><Button variant="outline" disabled={startBusy} onClick={closeStartDialog}>取消</Button><Button disabled={startBusy || !startDefaultSchemeId || !startDialogAssets.length} onClick={() => void startSelectedTranscriptions()}>{startBusy ? "启动中…" : `开始转录（${startDialogAssets.length}）`}</Button></DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={reTranscribeMediaIds.length > 0} onOpenChange={(open) => { if (!open) closeReTranscribeDialog(); }}>
        <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>配置重新转录方案</DialogTitle>
            <DialogDescription>为所选视频选择转录方案重新生成转录稿；与原方案一致时仍可继续，会给出提示。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            {retranscribeSameSchemeCount > 0 && <Alert role="status"><AlertTitle>所选方案与原方案一致</AlertTitle><AlertDescription>有 {retranscribeSameSchemeCount} 项所选转录方案与原方案一致，将按相同配置重新转录。</AlertDescription></Alert>}
            <ul className="divide-y divide-border rounded-ui-md border border-border" aria-label="重新转录的受影响视频">
              {reTranscribeDialogAssets.map((asset) => {
                const selectedId = reTranscribeOverrides[asset.media_id] || "";
                const sameAsOriginal = Boolean(asset.transcription_scheme_id && selectedId && selectedId === asset.transcription_scheme_id);
                return <li key={asset.media_id} className="grid gap-3 p-3 sm:grid-cols-[minmax(0,1fr)_minmax(12rem,18rem)] sm:items-center">
                  <div className="min-w-0">
                    <p className="truncate text-ui-sm font-medium">{asset.title}</p>
                    <p className="truncate text-ui-xs text-muted-foreground">{asset.original_filename}</p>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-ui-xs text-muted-foreground"><span>原方案：{asset.transcription_scheme_name || "原转录配置已删除"}</span>{asset.transcription_scheme_name && asset.transcription_scheme_deleted && <Badge variant="secondary">原转录配置已删除</Badge>}</div>
                    {sameAsOriginal && <p className="mt-1 text-ui-xs text-warning" role="status">所选方案与原转录方案一致，将按相同配置重新转录。</p>}
                  </div>
                  <Select aria-label={`${asset.title}的新转录方案`} value={selectedId} disabled={reTranscribeBusy} onChange={(event) => setReTranscribeOverrides((current) => ({ ...current, [asset.media_id]: event.target.value }))}>
                    <option value="">请选择转录方案</option>
                    {schemes.map((scheme) => <option key={scheme.scheme_id} value={scheme.scheme_id} disabled={!scheme.enabled || scheme.archived || scheme.availability !== "available"}>{scheme.name}{scheme.availability !== "available" ? "（不可用）" : ""}</option>)}
                  </Select>
                </li>;
              })}
            </ul>
          </div>
          <DialogFooter><Button variant="outline" disabled={reTranscribeBusy} onClick={closeReTranscribeDialog}>取消</Button><Button disabled={reTranscribeBusy || !reTranscribeDialogAssets.length} onClick={() => void startReTranscriptions()}>{reTranscribeBusy ? "提交中…" : `确认重新转录（${reTranscribeDialogAssets.length}）`}</Button></DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={Boolean(moveTarget)} onOpenChange={(open) => { if (!open && !moveBusy) { setMoveTarget(null); setMoveCategoryId(""); setMoveError(null); } }}>
        <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto"><DialogHeader><DialogTitle>调整归档目录</DialogTitle><DialogDescription>只调整资料库中的归档位置，不改变视频、转录稿、发布状态或索引。</DialogDescription></DialogHeader>{moveTarget && <CategoryTreePicker categories={categories} value={moveCategoryId} currentCategoryId={moveTarget.category_id} onChange={(categoryId) => { setMoveCategoryId(categoryId); setMoveError(null); }} label="目标目录" />}{moveError && <Alert variant="destructive" role="alert"><AlertTitle>调整失败</AlertTitle><AlertDescription>{moveError}</AlertDescription></Alert>}<DialogFooter><Button variant="outline" disabled={moveBusy} onClick={() => setMoveTarget(null)}>取消</Button><Button disabled={moveBusy || !moveCategoryId || moveCategoryId === moveTarget?.category_id} onClick={() => void moveMediaAsset()}>{moveBusy ? "处理中…" : "确认调整"}</Button></DialogFooter></DialogContent>
      </Dialog>
      <TranscriptionWorkbenchSheet
        open={selectedAsset != null}
        title={selectedAsset?.title || "转写工作台"}
        originalFilename={selectedAsset?.original_filename || ""}
        mediaId={selectedAsset?.media_id || null}
        schemeName={selectedAsset?.transcription_scheme_name || null}
        schemeDeleted={selectedAsset?.transcription_scheme_deleted || false}
        refreshToken={selectedAsset ? jobsByMediaId.get(selectedAsset.media_id)?.result_version_id : null}
        initialAction={workbenchAction}
        initialVersionId={workbenchVersionId}
        onClose={closeWorkbench}
        onChanged={refreshMediaState}
      />
      <Dialog open={replaceSourceMediaId != null} onOpenChange={(open) => { if (!open) closeReplacement(); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>替换视频</DialogTitle>
            <DialogDescription>新视频会作为独立候选完成转录、审核和发布；当前视频在候选正式发布前持续可用。</DialogDescription>
          </DialogHeader>
          {replaceSourceAsset && <div className="space-y-4">
            <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-2 text-ui-sm"><dt className="text-muted-foreground">当前资料</dt><dd className="break-words font-medium">{replaceSourceAsset.title}</dd><dt className="text-muted-foreground">当前文件</dt><dd className="break-all">{replaceSourceAsset.original_filename}</dd></dl>
            <label className="flex min-h-28 cursor-pointer flex-col items-center justify-center gap-2 rounded-ui-lg border border-dashed border-input bg-background px-4 py-4 text-center hover:bg-surface-muted focus-within:ring-2 focus-within:ring-ring"><FileUp className="size-6 text-primary" /><span className="text-ui-sm font-medium">{replacementFile?.name || "选择新的 MP4 视频"}</span>{replacementFile && <span className="text-ui-xs text-muted-foreground">{formatBytes(replacementFile.size)}</span>}<input type="file" className="sr-only" aria-label="选择替换视频" accept=".mp4,video/mp4" disabled={replacementBusy} onChange={(event) => { setReplacementFile(event.target.files?.[0] || null); setReplacementError(null); setReplacementProgress(0); }} /></label>
            <label className="block text-ui-sm font-medium">转录方案<Select aria-label="替换视频转录方案" className="mt-1" value={replacementProfileId} disabled={replacementBusy} onChange={(event) => setReplacementProfileId(event.target.value)}><option value="">请选择可用方案</option>{schemes.map((scheme) => <option key={scheme.scheme_id} value={scheme.scheme_id} disabled={!scheme.enabled || scheme.archived || scheme.availability !== "available"}>{scheme.name}{scheme.availability !== "available" ? "（不可用）" : ""}</option>)}</Select></label>
            {replacementBusy && <div className="space-y-1.5" role="status"><div className="flex items-center justify-between text-ui-xs"><span>{replacementProgress < 1 ? "正在上传候选视频" : "服务端正在准备转录任务"}</span><span className="tabular-nums text-muted-foreground">{Math.round(replacementProgress * 100)}%</span></div><Progress label="替换视频上传进度" value={replacementProgress < 1 ? replacementProgress * 100 : null} /></div>}
            {replacementError && <Alert variant="destructive" role="alert"><AlertTitle>替换任务创建失败</AlertTitle><AlertDescription>{replacementError}</AlertDescription></Alert>}
          </div>}
          <DialogFooter><Button variant="outline" disabled={replacementBusy} onClick={closeReplacement}>取消</Button><Button disabled={replacementBusy || !replacementFile || !replacementProfileId} onClick={() => void submitReplacement()}>{replacementBusy ? "提交中…" : "上传并开始转录"}</Button></DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={deleteTarget != null} onOpenChange={(open) => { if (!open && !deletingMediaId) setDeleteTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{staleExternalCleanupTarget ? "完成缓存清理" : "清理失败任务"}</DialogTitle>
            <DialogDescription>{staleExternalCleanupTarget ? `将只删除“${deleteTarget?.title}”上次清理遗留的暂存缓存；不会取消或修改当前转录任务，也不会删除共享目录原文件。` : deleteTarget?.storage_kind === "external" ? `将清理“${deleteTarget?.title}”的本地失败任务和派生缓存，重置为可重新入队；不会删除共享目录原文件。` : `将删除“${deleteTarget?.title}”的失败媒体、本地原始视频和失败任务历史。此操作不可恢复。`}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)} disabled={deletingMediaId != null}>取消</Button>
            <Button variant="destructive" onClick={() => deleteTarget && void deleteFailedMedia(deleteTarget)} disabled={deletingMediaId != null}>{deletingMediaId ? "清理中" : "确认清理"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={batchCleanupTargetIds.length > 0} onOpenChange={(open) => { if (!open && !batchActionBusy) setBatchCleanupTargetIds([]); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{batchCleanupHasStaleExternalCache ? "清理或收尾" : "清理"} {batchCleanupTargetIds.length} 个{batchCleanupHasStaleExternalCache ? "对象" : "失败任务"}</DialogTitle>
            <DialogDescription>普通受管媒体会删除本地媒体和失败历史；共享来源失败项只清理本地任务与派生缓存并重置为可重新入队，遗留暂存缓存则只完成上次清理收尾。服务端会逐项复核，绝不删除共享原文件或影响收尾对象的当前任务。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" disabled={batchActionBusy} onClick={() => setBatchCleanupTargetIds([])}>取消</Button>
            <Button variant="destructive" disabled={batchActionBusy} onClick={() => void runBatchCleanup()}>{batchActionBusy ? "清理中" : "确认批量清理"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={archiveTarget != null} onOpenChange={(open) => { if (!open && !deletingMediaId) setArchiveTarget(null); }}><DialogContent><DialogHeader><DialogTitle>将视频移入回收站？</DialogTitle><DialogDescription>“{archiveTarget?.title}”及其转写资料将从视频列表和知识库检索中隐藏，历史会保留，可从资料管理回收站恢复。</DialogDescription></DialogHeader><label className="flex items-start gap-2 rounded-ui-md border border-destructive/30 bg-destructive/5 p-3 text-ui-sm"><Checkbox checked={archiveAcknowledged} onChange={(event) => setArchiveAcknowledged(event.target.checked)} /><span>我已了解该视频移入回收站后将不再进入知识库检索。</span></label><DialogFooter><Button variant="outline" disabled={deletingMediaId != null} onClick={() => setArchiveTarget(null)}>取消</Button><Button variant="destructive" disabled={!archiveAcknowledged || deletingMediaId != null} onClick={() => archiveTarget && void archiveMedia(archiveTarget)}>{deletingMediaId ? "处理中…" : "确认移入回收站"}</Button></DialogFooter></DialogContent></Dialog>
      <h1 id="admin-media-title" className="sr-only">转录任务</h1>
    </section>
  );
}
