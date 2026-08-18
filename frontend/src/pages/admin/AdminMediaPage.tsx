import { useCallback, useEffect, useRef, useState } from "react";
import { ClipboardCheck, FileUp, Film, LoaderCircle, RefreshCw, Rocket, Settings2, Upload, XCircle } from "lucide-react";
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
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { TranscriptionWorkbenchSheet } from "../../components/TranscriptionWorkbenchSheet";
import { useTranscriptionJobs } from "../../hooks/useTranscriptionJobs";
import { useAdminMediaAssets } from "../../hooks/useAdminMediaAssets";
import { createRequestId } from "../../lib/request-id";
import type { MediaAsset, TranscriptionJob, TranscriptionProfile } from "../../types";
import { formatAdminDate, formatBytes } from "../../lib/admin-formatters";

type UploadMode = "manual" | "automatic";
type UploadState = "waiting" | "uploading" | "preparing" | "succeeded" | "failed";
type StatusVariant = "secondary" | "success" | "warning" | "destructive" | "info";
type MediaFilter = "all" | "processing" | "review" | "publishing" | "failed";

type PendingVideo = {
  id: string;
  file: File;
  title: string;
  selected: boolean;
  profileId: string;
  transcriptFile: File | null;
  transcriptText: string | null;
  requestId: string;
  state: UploadState;
  transferRatio: number;
  error: string | null;
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

const indexMeta: Record<string, { label: string; variant: StatusVariant }> = {
  pending: { label: "等待索引", variant: "secondary" },
  parsing: { label: "解析中", variant: "warning" },
  chunking: { label: "分块中", variant: "warning" },
  embedding: { label: "向量化中", variant: "warning" },
  done: { label: "索引成功", variant: "success" },
  failed: { label: "索引失败", variant: "destructive" },
};

const stageLabels: Record<string, string> = {
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
  if (job.status === "pending") return <p className="mt-1 text-ui-xs text-muted-foreground">{stage} · 等待服务调度</p>;

  const elapsedMs = Math.max(0, Date.now() - (job.started_at ?? job.created_at) * 1000);
  const hasCheckpoint = job.total_ms > 0 && job.processed_ms > 0 && job.processed_ms < job.total_ms;
  const progress = hasCheckpoint ? Math.min(100, Math.round((job.processed_ms / job.total_ms) * 100)) : null;
  const isTranscribing = job.stage === "transcribing";
  const durationDetail = job.total_ms > 0
    ? `视频时长 ${formatDuration(job.total_ms)}`
    : "正在读取视频时长";
  const detail = hasCheckpoint
    ? `${formatDuration(job.processed_ms)} / ${formatDuration(job.total_ms)}`
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
    { label: "索引", value: asset.publication_index_status, meta: indexMeta },
  ];
  return (
    <ol className="grid gap-1.5 sm:grid-cols-3" aria-label="审核、发布、索引流程">
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
    selected: true,
    profileId,
    transcriptFile: null,
    transcriptText: null,
    requestId: createRequestId(),
    state: "waiting",
    transferRatio: 0,
    error: null,
  };
}

export function AdminMediaPage() {
  const [deletingMediaId, setDeletingMediaId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<MediaAsset | null>(null);
  const [profiles, setProfiles] = useState<TranscriptionProfile[]>([]);
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
  const [mediaFilter, setMediaFilter] = useState<MediaFilter>("all");
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
    adminMediaApi.profiles()
      .then((items) => {
        setProfiles(items);
        const first = items.find((item) => item.admission === "enabled" && item.availability === "available");
        if (first) {
          setBulkProfileId(first.profile_id);
          setReplacementProfileId((current) => current || first.profile_id);
          setPending((current) => current.map((item) => item.profileId ? item : { ...item, profileId: first.profile_id }));
        }
      })
      .catch(() => setProfiles([]));
  }, []);

  const enabledProfiles = profiles.filter((item) => item.admission === "enabled" && item.availability === "available");
  const editingItem = pending.find((item) => item.id === editingId);

  function addVideos(files: FileList | File[]) {
    const next = Array.from(files)
      .filter((file) => file.type === "video/mp4" || file.name.toLowerCase().endsWith(".mp4"))
      .map((file) => pendingFromFile(file, bulkProfileId || enabledProfiles[0]?.profile_id || ""));
    if (next.length) setPending((current) => [...current, ...next]);
  }

  function updatePending(id: string, change: Partial<PendingVideo>) {
    setPending((current) => current.map((item) => item.id === id ? { ...item, ...change } : item));
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
      const profile = profiles.find((entry) => entry.profile_id === item.profileId);
      if (!profile || profile.admission !== "enabled" || profile.availability !== "available") return "请选择可用的服务端 Profile";
    } else {
      if (!item.transcriptFile || item.transcriptText === null) return "请绑定 Markdown 转写文件";
      if (!item.transcriptFile.name.toLowerCase().endsWith(".md")) return "转写文件必须是 .md";
      if (!/说话[人⼈]\s+\d+\s+\d{1,2}:\d{2}/.test(item.transcriptText)) return "Markdown 缺少“说话人 HH:MM:SS”格式标记";
    }
    return null;
  }

  const readyItems = pending.filter((item) => item.state !== "succeeded");
  const canSubmit = Boolean(mode && readyItems.length && readyItems.every((item) => !validateItem(item)) && !submitting);
  const mediaFilterOptions = [
    ["all", "全部"],
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
  const visibleMediaAssets = mediaAssets.filter((asset) => {
    return matchesMediaFilter(asset, mediaFilter);
  });
  const filterCounts = mediaFilterOptions.reduce<Record<MediaFilter, number>>((counts, [value]) => {
    counts[value] = mediaAssets.filter((asset) => matchesMediaFilter(asset, value)).length;
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
      await adminMediaApi.deleteFailedAsset(asset.media_id);
      removeAsset(asset.media_id);
      setDeleteTarget(null);
    } catch (e: any) {
      setUploadError(e?.message || String(e));
    } finally {
      setDeletingMediaId(null);
    }
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
        const uploaded = await adminMediaApi.uploadAutomatic(item.file, item.title.trim(), item.profileId, item.requestId, callbacks);
        if (uploaded.transcription_job_id) replaceJob(await adminMediaApi.getJob(uploaded.transcription_job_id));
      } else {
        const transcript = new File(
          [item.transcriptText!],
          item.transcriptFile!.name,
          { type: "text/markdown" },
        );
        await adminMediaApi.uploadManual(item.file, transcript, item.title.trim(), callbacks);
      }
      updatePending(item.id, { state: "succeeded", transferRatio: 1, error: null });
    } catch (e: any) {
      updatePending(item.id, { state: "failed", error: e?.message || String(e) });
    }
  }

  async function submitBatch() {
    if (!canSubmit) return;
    setSubmitting(true);
    setUploadError(null);
    const queue = [...readyItems];
    setActiveBatchIds(queue.map((item) => item.id));
    let cursor = 0;
    const worker = async () => {
      while (cursor < queue.length) {
        const item = queue[cursor++];
        await uploadOne(item);
      }
    };
    await Promise.all(Array.from({ length: Math.min(2, queue.length) }, worker));
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

  async function retryJob(job: TranscriptionJob) {
    let requestKey = retryIdempotencyKeys.current.get(job.job_id);
    if (!requestKey) {
      requestKey = createRequestId();
      retryIdempotencyKeys.current.set(job.job_id, requestKey);
    }
    try {
      replaceJob(await adminMediaApi.retryJob(job.media_id, job.profile_id, requestKey));
      retryIdempotencyKeys.current.delete(job.job_id);
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
  const batchSettledCount = activeBatchItems.filter((item) => item.state === "succeeded" || item.state === "failed").length;
  const batchUploadingCount = activeBatchItems.filter((item) => item.state === "uploading").length;
  const batchPreparingCount = activeBatchItems.filter((item) => item.state === "preparing").length;
  const hasUploadDraft = pending.length > 0 || submitting;
  const resetUploadDraft = () => { setStep(1); setMode(null); setPending([]); setEditingId(null); setSubmitting(false); setActiveBatchIds([]); setUploadError(null); };
  const requestUploadDialogClose = (open: boolean) => {
    if (open) { setUploadDialogOpen(true); return; }
    if (hasUploadDraft && !submitting && !window.confirm("当前上传流程尚未完成，关闭窗口后进度会保留。点击“确定”关闭并保留进度，点击“取消”继续操作。")) return;
    setUploadDialogOpen(false);
  };

  return (
    <section className="space-y-6" aria-labelledby="admin-media-title">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-ui-xs font-medium text-primary">内容管理</p>
          <h1 id="admin-media-title" className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">视频管理</h1>
          <p className="mt-1 max-w-3xl text-ui-sm text-muted-foreground">分步骤批量上传视频，并选择人工 Markdown 或受控服务端 Profile。</p>
        </div>
        <a className={buttonVariants({ variant: "outline" })} href="/admin/asr"><Settings2 className="size-4" />转录配置</a>
      </header>

      <Dialog open={uploadDialogOpen} onOpenChange={requestUploadDialogClose}>
      <DialogContent className="max-h-[90vh] max-w-4xl overflow-y-auto">
      <Card className="border-0 shadow-none">
        <CardHeader className="p-4 pb-3 sm:p-5 sm:pb-4">
          <CardTitle className="text-ui-lg">上传视频与转写</CardTitle>
          <CardDescription className="mt-1">自动转录成功不代表已经审核、发布或进入索引。</CardDescription>
          <ol className="mt-3 grid grid-cols-3 gap-1.5 sm:gap-2" aria-label="上传步骤">
            {["上传视频", "转写方式", "配置并提交"].map((label, index) => (
              <li key={label} className={`rounded-ui-md border px-2 py-1.5 text-ui-xs sm:px-3 sm:py-2 sm:text-ui-sm ${step === index + 1 ? "border-primary bg-primary/10 font-medium text-primary" : "border-border text-muted-foreground"}`}>
                {index + 1}. {label}
              </li>
            ))}
          </ol>
        </CardHeader>
        <CardContent className="space-y-4 px-4 pb-4 pt-0 sm:space-y-5 sm:px-5 sm:pb-5">
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
                  <span className="mt-2 block text-ui-sm text-muted-foreground">使用服务端白名单 Profile 生成候选草稿。实验性 Profile 强制人工审核。</span>
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
              <div className="flex flex-wrap items-end gap-3">
                <Button variant="outline" size="sm" onClick={() => setPending((current) => current.map((item) => ({ ...item, selected: true })))}>全选</Button>
                <Button variant="outline" size="sm" onClick={() => setPending((current) => current.map((item) => ({ ...item, selected: false })))}>取消全选</Button>
                {mode === "automatic" && (
                  <>
                    <label className="text-ui-sm font-medium">批量转录 Profile
                      <select aria-label="批量转录 Profile" value={bulkProfileId} onChange={(event) => setBulkProfileId(event.target.value)} className="mt-1 block h-control-md min-w-64 rounded-ui-md border border-input bg-background px-3 text-ui-sm">
                        <option value="">请选择服务端 Profile</option>
                        {profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id} disabled={profile.admission !== "enabled" || profile.availability !== "available"}>{profile.display_name}{profile.qualification === "experimental" ? "（实验性·强制审核）" : ""}{profile.availability !== "available" ? "（不可用）" : ""}</option>)}
                      </select>
                    </label>
                    <Button variant="outline" onClick={() => setPending((current) => current.map((item) => item.selected ? { ...item, profileId: bulkProfileId, requestId: createRequestId(), state: "waiting", error: null } : item))}>应用到已选择视频</Button>
                  </>
                )}
              </div>

              <div className="max-h-[32rem] space-y-3 overflow-y-auto pr-1" aria-label="上传配置列表">
                {pending.map((item) => {
                  const validationError = validateItem(item);
                  const profile = profiles.find((entry) => entry.profile_id === item.profileId);
                  return (
                    <div key={item.id} className="rounded-ui-xl border border-border p-4">
                      <div className="flex gap-3">
                        <input aria-label={`选择 ${item.file.name}`} type="checkbox" checked={item.selected} onChange={(event) => updatePending(item.id, { selected: event.target.checked })} />
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-medium">{item.file.name}</p>
                          <p className="text-ui-xs text-muted-foreground">{formatBytes(item.file.size)}</p>
                        </div>
                        <StatusBadge value={item.state} meta={{ waiting: { label: "待提交", variant: "secondary" }, uploading: { label: "上传中", variant: "warning" }, preparing: { label: "服务端处理中", variant: "info" }, succeeded: { label: "已提交", variant: "success" }, failed: { label: "提交失败", variant: "destructive" } }} />
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
                      <div className="mt-3 grid gap-3 lg:grid-cols-2">
                        <label className="text-ui-sm font-medium">视频标题
                          <Input aria-label={`${item.file.name} 的视频标题`} className="mt-1" value={item.title} disabled={submitting || item.state === "succeeded"} onChange={(event) => updatePending(item.id, { title: event.target.value, requestId: createRequestId(), state: "waiting", error: null })} />
                        </label>
                        {mode === "automatic" ? (
                          <label className="text-ui-sm font-medium">转录 Profile
                            <select aria-label={`${item.file.name} 的转录 Profile`} className="mt-1 h-control-md w-full rounded-ui-md border border-input bg-background px-3 text-ui-sm" value={item.profileId} disabled={submitting || item.state === "succeeded"} onChange={(event) => updatePending(item.id, { profileId: event.target.value, requestId: createRequestId(), state: "waiting", error: null })}>
                              <option value="">请选择服务端 Profile</option>
                              {profiles.map((entry) => <option key={entry.profile_id} value={entry.profile_id} disabled={entry.admission !== "enabled" || entry.availability !== "available"}>{entry.display_name}{entry.qualification === "experimental" ? "（实验性·强制审核）" : ""}{entry.availability !== "available" ? "（不可用）" : ""}</option>)}
                            </select>
                            {profile?.requires_review && <span className="mt-1 block text-ui-xs text-warning">此 Profile 生成的草稿必须人工审核，不能自动发布或索引。</span>}
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
                      {(item.error || validationError) && <p className="mt-2 text-ui-xs text-destructive">{item.error || validationError}</p>}
                    </div>
                  );
                })}
              </div>

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

              <div className="flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={submitting} onClick={() => setStep(2)}>返回选择方式</Button><Button variant="ghost" disabled={submitting} onClick={() => { if (window.confirm("确定取消本次上传并清空已选择的文件吗？")) { resetUploadDraft(); setUploadDialogOpen(false); } }}>取消上传</Button></div>
                <div className="text-right">
                  <p className="mb-2 text-ui-xs text-muted-foreground">{mode === "manual" ? "保持现有人工 Markdown 上传与索引路径。" : "每个文件使用独立幂等键；最多并发上传 2 个。"}</p>
                  <Button disabled={!canSubmit} onClick={() => void submitBatch()}>{submitting ? "正在批量提交…" : mode === "manual" ? "上传视频与人工转写" : "上传并创建自动转录任务"}</Button>
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>
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

      {uploadError && <Alert variant="destructive" role="alert"><AlertTitle>操作失败</AlertTitle><AlertDescription>{uploadError}</AlertDescription></Alert>}

      <section className="space-y-3" aria-labelledby="media-assets-title">
          <div className="flex flex-wrap items-end justify-between gap-3">
          <div><h2 id="media-assets-title" className="text-ui-base font-semibold">媒体资源</h2><p className="mt-1 flex flex-wrap gap-x-2 text-ui-xs text-muted-foreground"><span>共 {mediaAssets.length} 个视频</span><span>·</span><span>按每次提交分别记录媒体与处理进度；同名文件不会合并。</span></p></div>
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={() => setUploadDialogOpen(true)}><Upload className="size-4" />{hasUploadDraft ? `继续上传${pending.length ? `（${pending.length}）` : ""}` : "上传视频"}</Button>
            <Button size="sm" variant="outline" aria-label="刷新媒体资源" title="刷新媒体资源" disabled={loading} onClick={() => void refreshMediaState()}>
              <RefreshCw className="size-4" aria-hidden="true" />
              刷新
            </Button>
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5" aria-label="媒体快捷筛选">
          {mediaFilterOptions.map(([value, label]) => { const icons = { all: <Film className="size-4" />, processing: <LoaderCircle className="size-4" />, review: <ClipboardCheck className="size-4" />, publishing: <Rocket className="size-4" />, failed: <XCircle className="size-4" /> }; return (
            <button type="button" key={value} className={`rounded-ui-lg border bg-background p-3 text-left transition-colors hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${mediaFilter === value ? "border-primary ring-1 ring-primary/30" : "border-border"}`} aria-pressed={mediaFilter === value} aria-label={`${label} ${filterCounts[value]} 条`} onClick={() => setMediaFilter(value)}><span className="flex items-center justify-between text-ui-xs text-muted-foreground"><span>{label}</span>{icons[value]}</span><span className="mt-2 block text-ui-xl font-semibold tabular-nums">{filterCounts[value]}</span></button>
          ); })}
        </div>
        <p className="text-ui-xs text-muted-foreground">当前显示 {visibleMediaAssets.length} / {mediaAssets.length} 条记录{lastLoadedAt ? ` · 最近刷新 ${new Date(lastLoadedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}` : ""}。</p>
        {jobsError && <Alert role="alert"><AlertTitle>任务状态暂时无法刷新</AlertTitle><AlertDescription>{jobsError}</AlertDescription></Alert>}
        {loadError ? <ErrorState title="媒体资源加载失败" description={loadError} action={<Button variant="outline" size="sm" onClick={refresh}>重新加载</Button>} />
          : loading ? <Card><LoadingState className="min-h-48" label="正在加载媒体资源…" /></Card>
          : mediaAssets.length === 0 ? <EmptyState title="暂无媒体资源" description="完成向导后，视频和各阶段状态会显示在这里。" />
          : <Card className="overflow-hidden shadow-surface">
            <div className="hidden grid-cols-[minmax(0,31fr)_minmax(0,42fr)_minmax(0,12fr)_minmax(0,15fr)] gap-4 border-b border-border bg-surface-muted px-5 py-3 text-ui-xs font-medium text-muted-foreground lg:grid" data-testid="media-record-header">
              <span>媒体信息</span><span>处理进度</span><span>最近提交</span><span>操作</span>
            </div>
            <ul className="divide-y divide-border" aria-label="视频处理记录">
              {visibleMediaAssets.map((asset) => {
                const job = jobsByMediaId.get(asset.media_id);
                const sameNameCount = mediaAssets.filter((item) => item.original_filename === asset.original_filename).length;
                const canDelete = asset.status === "failed" && !job;
                return <li key={asset.media_id} className="p-4 sm:p-5" data-testid="media-record-row">
                  <div className="grid gap-4 lg:grid-cols-[minmax(0,31fr)_minmax(0,42fr)_minmax(0,12fr)_minmax(0,15fr)] lg:items-start">
                    <div className="min-w-0">
                      <p className="truncate font-medium" title={asset.title}>{asset.title}</p>
                      <p className="mt-1 truncate font-mono text-ui-xs text-muted-foreground" title={asset.original_filename}>{asset.original_filename}</p>
                      <div className="mt-2 flex flex-wrap gap-2 text-ui-xs text-muted-foreground"><span>{formatBytes(asset.file_size)}</span>{sameNameCount > 1 && <Badge variant="secondary">同名记录 {sameNameCount} 条</Badge>}{asset.replacement_source_media_id && <Badge variant={asset.replacement_status === "activated" ? "success" : asset.replacement_status === "failed" ? "destructive" : "warning"}>{asset.replacement_status === "activated" ? "替换已生效" : asset.replacement_status === "failed" ? "替换候选失败" : "替换候选"}</Badge>}{asset.replacement_candidate_media_id && asset.replacement_status === "pending" && <Badge variant="warning">替换处理中</Badge>}</div>
                    </div>
                    <div className="min-w-0 space-y-2">
                      <div className="flex flex-wrap items-center gap-2"><StatusBadge value={job?.status || asset.status} meta={job ? jobStatusMeta : mediaStatusMeta} /></div>
                      {asset.error && !job && <p className="text-ui-xs text-destructive">媒体处理失败，请在确认后删除或重新提交。</p>}
                      {job ? <JobSummary job={job} /> : <p className="text-ui-xs text-muted-foreground">{asset.transcript_origin === "manual" ? "人工转写" : "尚未创建转录任务"}</p>}
                      <LifecycleRail asset={asset} />
                    </div>
                    <p className="text-ui-xs text-muted-foreground"><span className="sr-only">提交时间：</span>{formatAdminDate(asset.created_at)}</p>
                    <div className="flex flex-wrap gap-1.5 lg:justify-end" aria-label={`媒体操作：${asset.title}`}>
                      <Button className="min-h-10 sm:min-h-0" size="sm" variant="outline" onClick={() => openWorkbench(asset.media_id)}>进入转写工作台</Button>
                      {(job?.status === "pending" || job?.status === "running") && <Button className="min-h-10 sm:min-h-0" size="sm" variant="outline" onClick={() => void cancelJob(job)}>取消</Button>}
                      {(job?.status === "failed" || job?.status === "cancelled") && job.failure?.retryable !== false && <Button className="min-h-10 sm:min-h-0" size="sm" variant="outline" onClick={() => void retryJob(job)}>重试</Button>}
                      {canDelete && <Button className="min-h-10 sm:min-h-0" size="sm" variant="destructive" disabled={deletingMediaId === asset.media_id} onClick={() => setDeleteTarget(asset)}>{deletingMediaId === asset.media_id ? "删除中" : "完整删除"}</Button>}
                    </div>
                  </div>
                </li>;
              })}
            </ul>
          </Card>}
        {visibleMediaAssets.length === 0 && mediaAssets.length > 0 && <EmptyState title="没有符合条件的媒体" description="请切换其他快捷筛选条件。" />}
      </section>
      <TranscriptionWorkbenchSheet
        open={selectedAsset != null}
        title={selectedAsset?.title || "转写工作台"}
        originalFilename={selectedAsset?.original_filename || ""}
        mediaId={selectedAsset?.media_id || null}
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
            <label className="block text-ui-sm font-medium">转录 Profile<select aria-label="替换视频转录 Profile" className="mt-1 h-control-md w-full rounded-ui-md border border-input bg-background px-3 text-ui-sm" value={replacementProfileId} disabled={replacementBusy} onChange={(event) => setReplacementProfileId(event.target.value)}><option value="">请选择可用 Profile</option>{profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id} disabled={profile.admission !== "enabled" || profile.availability !== "available"}>{profile.display_name}{profile.qualification === "experimental" ? "（实验性·强制审核）" : ""}{profile.availability !== "available" ? "（不可用）" : ""}</option>)}</select></label>
            {replacementBusy && <div className="space-y-1.5" role="status"><div className="flex items-center justify-between text-ui-xs"><span>{replacementProgress < 1 ? "正在上传候选视频" : "服务端正在准备转录任务"}</span><span className="tabular-nums text-muted-foreground">{Math.round(replacementProgress * 100)}%</span></div><Progress label="替换视频上传进度" value={replacementProgress < 1 ? replacementProgress * 100 : null} /></div>}
            {replacementError && <Alert variant="destructive" role="alert"><AlertTitle>替换任务创建失败</AlertTitle><AlertDescription>{replacementError}</AlertDescription></Alert>}
          </div>}
          <DialogFooter><Button variant="outline" disabled={replacementBusy} onClick={closeReplacement}>取消</Button><Button disabled={replacementBusy || !replacementFile || !replacementProfileId} onClick={() => void submitReplacement()}>{replacementBusy ? "提交中…" : "上传并开始转录"}</Button></DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={deleteTarget != null} onOpenChange={(open) => { if (!open && !deletingMediaId) setDeleteTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>完整删除失败视频</DialogTitle>
            <DialogDescription>将删除“{deleteTarget?.title}”的媒体记录和原始视频。此操作不可恢复。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)} disabled={deletingMediaId != null}>取消</Button>
            <Button variant="destructive" onClick={() => deleteTarget && void deleteFailedMedia(deleteTarget)} disabled={deletingMediaId != null}>{deletingMediaId ? "删除中" : "完整删除"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
