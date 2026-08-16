import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "../../api/client";
import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { Input } from "../../components/ui/input";
import { LoadingState } from "../../components/ui/loading-state";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { TranscriptionWorkbenchSheet } from "../../components/TranscriptionWorkbenchSheet";
import { useTranscriptionJobs } from "../../hooks/useTranscriptionJobs";
import { createRequestId } from "../../lib/request-id";
import type { MediaAsset, TranscriptionJob, TranscriptionProfile } from "../../types";
import { formatAdminDate, formatBytes } from "./admin-formatters";

type UploadMode = "manual" | "automatic";
type UploadState = "waiting" | "uploading" | "succeeded" | "failed";
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

function JobSummary({ job }: { job: TranscriptionJob }) {
  if (job.status === "succeeded") return <p className="mt-1 text-ui-xs text-muted-foreground">草稿已生成，等待后续审核与发布。</p>;
  if (job.status === "failed") return <p className="mt-1 text-ui-xs text-destructive">{job.failure?.message || job.error_summary || job.failure_error_code || "转录失败"}</p>;
  if (job.status === "cancelled") return <p className="mt-1 text-ui-xs text-muted-foreground">任务已取消，可重新转录。</p>;
  const stage = job.stage ? stageLabels[job.stage] || job.stage : "排队中";
  const progress = job.total_ms > 0 ? Math.min(100, Math.round((job.processed_ms / job.total_ms) * 100)) : 0;
  return <p className="mt-1 text-ui-xs text-muted-foreground">{stage} · {progress}%</p>;
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
    error: null,
  };
}

export function AdminMediaPage() {
  const [mediaAssets, setMediaAssets] = useState<MediaAsset[]>([]);
  const [deletingMediaId, setDeletingMediaId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<MediaAsset | null>(null);
  const [profiles, setProfiles] = useState<TranscriptionProfile[]>([]);
  const [step, setStep] = useState(1);
  const [mode, setMode] = useState<UploadMode | null>(null);
  const [pending, setPending] = useState<PendingVideo[]>([]);
  const [bulkProfileId, setBulkProfileId] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [mediaFilter, setMediaFilter] = useState<MediaFilter>("all");
  const [lastLoadedAt, setLastLoadedAt] = useState<number | null>(null);
  const [selectedMediaId, setSelectedMediaId] = useState<string | null>(null);
  const retryIdempotencyKeys = useRef(new Map<string, string>());
  const previousJobStatuses = useRef(new Map<string, string>());
  const { jobs, jobsByMediaId, error: jobsError, refreshJobs, replaceJob } = useTranscriptionJobs();

  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setMediaAssets(await api.listMediaAssets());
      setLastLoadedAt(Date.now());
    } catch (e: any) {
      setLoadError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
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
    api.listTranscriptionProfiles()
      .then((items) => {
        setProfiles(items);
        const first = items.find((item) => item.admission === "enabled" && item.availability === "available");
        if (first) {
          setBulkProfileId(first.profile_id);
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
  const refreshMediaState = useCallback(async () => {
    await Promise.all([refresh(), refreshJobs()]);
  }, [refresh, refreshJobs]);

  useEffect(() => {
    if (selectedMediaId && !mediaAssets.some((asset) => asset.media_id === selectedMediaId)) setSelectedMediaId(null);
  }, [mediaAssets, selectedMediaId]);

  async function deleteFailedMedia(asset: MediaAsset) {
    setDeletingMediaId(asset.media_id);
    try {
      await api.deleteFailedMediaAsset(asset.media_id);
      setMediaAssets((items) => items.filter((item) => item.media_id !== asset.media_id));
      setDeleteTarget(null);
    } catch (e: any) {
      setUploadError(e?.message || String(e));
    } finally {
      setDeletingMediaId(null);
    }
  }

  async function uploadOne(item: PendingVideo) {
    updatePending(item.id, { state: "uploading", error: null });
    try {
      if (mode === "automatic") {
        const uploaded = await api.uploadAutomaticMediaVideo(item.file, item.title.trim(), item.profileId, item.requestId);
        if (uploaded.transcription_job_id) replaceJob(await api.getTranscriptionJob(uploaded.transcription_job_id));
      } else {
        const transcript = new File(
          [item.transcriptText!],
          item.transcriptFile!.name,
          { type: "text/markdown" },
        );
        await api.uploadMediaVideo(item.file, transcript, item.title.trim());
      }
      updatePending(item.id, { state: "succeeded", error: null });
    } catch (e: any) {
      updatePending(item.id, { state: "failed", error: e?.message || String(e) });
    }
  }

  async function submitBatch() {
    if (!canSubmit) return;
    setSubmitting(true);
    setUploadError(null);
    const queue = [...readyItems];
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
      replaceJob(await api.cancelTranscriptionJob(job.job_id));
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
      replaceJob(await api.retryTranscription(job.media_id, job.profile_id, requestKey));
      retryIdempotencyKeys.current.delete(job.job_id);
      await refresh();
    } catch (e: any) {
      setUploadError(e?.message || String(e));
    }
  }

  return (
    <section className="space-y-6" aria-labelledby="admin-media-title">
      <header>
        <p className="text-ui-xs font-medium text-primary">内容管理</p>
        <h1 id="admin-media-title" className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">视频管理</h1>
        <p className="mt-1 max-w-3xl text-ui-sm text-muted-foreground">分步骤批量上传视频，并选择人工 Markdown 或受控服务端 Profile。</p>
      </header>

      <Card className="shadow-surface">
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
                        <StatusBadge value={item.state} meta={{ waiting: { label: "待提交", variant: "secondary" }, uploading: { label: "上传中", variant: "warning" }, succeeded: { label: "已提交", variant: "success" }, failed: { label: "提交失败", variant: "destructive" } }} />
                      </div>
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
                              {item.transcriptFile && <Button variant="outline" onClick={() => setEditingId(item.id)}>打开并编辑</Button>}
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

              <div className="flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-center sm:justify-between">
                <Button variant="outline" disabled={submitting} onClick={() => setStep(2)}>返回选择方式</Button>
                <div className="text-right">
                  <p className="mb-2 text-ui-xs text-muted-foreground">{mode === "manual" ? "保持现有人工 Markdown 上传与索引路径。" : "每个文件使用独立幂等键；最多并发上传 2 个。"}</p>
                  <Button disabled={!canSubmit} onClick={() => void submitBatch()}>{submitting ? "正在批量提交…" : mode === "manual" ? "上传视频与人工转写" : "上传并创建自动转录任务"}</Button>
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {editingItem && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-labelledby="transcript-editor-title">
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
          <div><h2 id="media-assets-title" className="text-ui-base font-semibold">媒体资源</h2><p className="mt-1 text-ui-xs text-muted-foreground">按每次提交分别记录媒体与处理进度；同名文件不会合并。</p></div>
          <div className="flex items-center gap-2">
            <span className="text-ui-xs text-muted-foreground">共 {mediaAssets.length} 个视频</span>
            <Button size="sm" variant="outline" aria-label="刷新媒体资源" title="刷新媒体资源" disabled={loading} onClick={() => void Promise.all([refresh(), refreshJobs()])}>
              <RefreshCw className="size-4" aria-hidden="true" />
              刷新
            </Button>
          </div>
        </div>
        <div className="flex flex-wrap gap-2" aria-label="媒体快捷筛选">
          {mediaFilterOptions.map(([value, label]) => (
            <Button key={value} size="sm" variant={mediaFilter === value ? "default" : "outline"} aria-pressed={mediaFilter === value} aria-label={`${label} ${filterCounts[value]} 条`} onClick={() => setMediaFilter(value)}>
              {label}<span className="text-ui-xs opacity-75">{filterCounts[value]}</span>
            </Button>
          ))}
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
                      <div className="mt-2 flex flex-wrap gap-2 text-ui-xs text-muted-foreground"><span>{formatBytes(asset.file_size)}</span>{sameNameCount > 1 && <Badge variant="secondary">同名记录 {sameNameCount} 条</Badge>}</div>
                    </div>
                    <div className="min-w-0 space-y-2">
                      <div className="flex flex-wrap items-center gap-2"><StatusBadge value={job?.status || asset.status} meta={job ? jobStatusMeta : mediaStatusMeta} /></div>
                      {asset.error && !job && <p className="text-ui-xs text-destructive">媒体处理失败，请在确认后删除或重新提交。</p>}
                      {job ? <JobSummary job={job} /> : <p className="text-ui-xs text-muted-foreground">{asset.transcript_origin === "manual" ? "人工转写" : "尚未创建转录任务"}</p>}
                      <LifecycleRail asset={asset} />
                    </div>
                    <p className="text-ui-xs text-muted-foreground"><span className="sr-only">提交时间：</span>{formatAdminDate(asset.created_at)}</p>
                    <div className="flex flex-wrap gap-1.5 lg:justify-end" aria-label={`媒体操作：${asset.title}`}>
                      <Button className="min-h-10 sm:min-h-0" size="sm" variant="outline" onClick={() => setSelectedMediaId(asset.media_id)}>进入转写工作台</Button>
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
        onClose={() => setSelectedMediaId(null)}
        onChanged={refreshMediaState}
      />
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
