import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { Input } from "../../components/ui/input";
import { LoadingState } from "../../components/ui/loading-state";
import { useTranscriptionJobs } from "../../hooks/useTranscriptionJobs";
import { createRequestId } from "../../lib/request-id";
import type { MediaAsset, TranscriptionJob, TranscriptionProfile } from "../../types";
import { formatAdminDate, formatBytes } from "./admin-formatters";

type UploadMode = "manual" | "automatic";
type StatusVariant = "secondary" | "success" | "warning" | "destructive" | "info";

const mediaStatusMeta: Record<string, { label: string; variant: StatusVariant }> = {
  uploaded: { label: "已上传", variant: "secondary" },
  uploading: { label: "上传中", variant: "info" },
  transcribing: { label: "自动转录中", variant: "warning" },
  transcript_ready: { label: "转写草稿就绪", variant: "info" },
  indexing: { label: "索引中", variant: "warning" },
  ready: { label: "已就绪", variant: "success" },
  failed: { label: "失败", variant: "destructive" },
};

const stageLabels: Record<string, string> = {
  validating_input: "校验输入",
  transcribing: "转录中",
  normalizing: "规范化",
  formatting: "生成 Markdown",
};

function MediaStatusBadge({ status }: { status: string }) {
  const meta = mediaStatusMeta[status];
  return <Badge variant={meta?.variant ?? "secondary"}>{meta?.label ?? status}</Badge>;
}

function JobSummary({ job }: { job: TranscriptionJob }) {
  if (job.status === "succeeded") {
    return <p className="mt-1 text-ui-xs text-muted-foreground">转录草稿已生成，等待人工审核；尚未发布，也未进入索引。</p>;
  }
  if (job.status === "failed") {
    return <p className="mt-1 text-ui-xs text-destructive">{job.error_summary || job.failure_error_code || "转录失败"}</p>;
  }
  if (job.status === "cancelled") {
    return <p className="mt-1 text-ui-xs text-muted-foreground">任务已取消，可重新转录。</p>;
  }
  const stage = job.stage ? stageLabels[job.stage] || job.stage : "排队中";
  const progress = job.total_ms > 0 ? Math.min(100, Math.round((job.processed_ms / job.total_ms) * 100)) : 0;
  return <p className="mt-1 text-ui-xs text-muted-foreground">{stage} · {progress}%</p>;
}

export function AdminMediaPage() {
  const [mediaAssets, setMediaAssets] = useState<MediaAsset[]>([]);
  const [profiles, setProfiles] = useState<TranscriptionProfile[]>([]);
  const [mode, setMode] = useState<UploadMode>("manual");
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [transcriptFile, setTranscriptFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [profileId, setProfileId] = useState("");
  const idempotencyKey = useRef(createRequestId());
  const retryIdempotencyKeys = useRef(new Map<string, string>());
  const videoInputRef = useRef<HTMLInputElement>(null);
  const transcriptInputRef = useRef<HTMLInputElement>(null);
  const { jobsByMediaId, error: jobsError, refreshJobs, replaceJob } = useTranscriptionJobs();

  const rotateRequestIdentity = () => { idempotencyKey.current = createRequestId(); };

  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const assets = await api.listMediaAssets();
      setMediaAssets(assets);
    } catch (e: any) {
      setLoadError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    api.listTranscriptionProfiles()
      .then((items) => {
        setProfiles(items);
        const first = items.find((item) => item.admission === "enabled" && item.availability === "available");
        if (first) setProfileId((current) => current || first.profile_id);
      })
      .catch(() => setProfiles([]));
  }, []);

  async function handleUpload() {
    if (!videoFile || !title.trim()) return;
    if (mode === "manual" && !transcriptFile) return;
    if (mode === "automatic" && !profileId) return;
    setUploading(true);
    setUploadError(null);
    try {
      if (mode === "manual") {
        await api.uploadMediaVideo(videoFile, transcriptFile!, title.trim());
      } else {
        const uploaded = await api.uploadAutomaticMediaVideo(videoFile, title.trim(), profileId, idempotencyKey.current);
        if (uploaded.transcription_job_id) {
          replaceJob(await api.getTranscriptionJob(uploaded.transcription_job_id));
        } else {
          await refreshJobs();
        }
      }
      setVideoFile(null);
      setTranscriptFile(null);
      setTitle("");
      rotateRequestIdentity();
      if (videoInputRef.current) videoInputRef.current.value = "";
      if (transcriptInputRef.current) transcriptInputRef.current.value = "";
      await refresh();
    } catch (e: any) {
      setUploadError(e?.message || String(e));
    } finally {
      setUploading(false);
    }
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

  const selectedProfile = profiles.find((item) => item.profile_id === profileId);
  const automaticProfileReady = selectedProfile?.admission === "enabled" && selectedProfile.availability === "available";
  const canUpload = Boolean(
    videoFile && title.trim() && !uploading &&
    (mode === "manual" ? transcriptFile : automaticProfileReady),
  );

  return (
    <section className="space-y-6" aria-labelledby="admin-media-title">
      <header>
        <p className="text-ui-xs font-medium uppercase tracking-[0.14em] text-primary">媒体与转写</p>
        <h1 id="admin-media-title" className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">视频媒体</h1>
        <p className="mt-1 max-w-3xl text-ui-sm text-muted-foreground">上传人工转写，或仅上传 MP4 并使用受控服务端 Profile 生成待审核转录草稿。</p>
      </header>

      <Card className="shadow-surface">
        <CardHeader className="p-5 pb-4">
          <CardTitle className="text-ui-lg">上传视频与转写</CardTitle>
          <CardDescription className="mt-1">自动转录成功不代表已经审核、发布或进入索引。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5 px-5 pb-5 pt-0">
          <div className="flex gap-2" aria-label="转写方式">
            <Button variant={mode === "manual" ? "default" : "outline"} onClick={() => setMode("manual")}>人工转写</Button>
            <Button variant={mode === "automatic" ? "default" : "outline"} onClick={() => setMode("automatic")}>自动转录</Button>
          </div>
          <div>
            <label htmlFor="media-title" className="mb-1.5 block text-ui-sm font-medium">视频标题</label>
            <Input id="media-title" value={title} onChange={(event) => { setTitle(event.target.value); rotateRequestIdentity(); }} disabled={uploading} />
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="rounded-ui-xl border border-border bg-surface-muted/40 p-4">
              <label htmlFor="media-video-file" className="block text-ui-sm font-medium">视频文件</label>
              <Input ref={videoInputRef} id="media-video-file" type="file" accept=".mp4,video/mp4" disabled={uploading}
                onChange={(event) => { setVideoFile(event.target.files?.[0] || null); rotateRequestIdentity(); }} className="mt-3 h-auto min-h-control-md py-1.5" />
              <p className="mt-2 text-ui-xs text-muted-foreground">{videoFile ? `${videoFile.name} · ${formatBytes(videoFile.size)}` : "尚未选择视频文件"}</p>
            </div>
            {mode === "manual" ? (
              <div className="rounded-ui-xl border border-border bg-surface-muted/40 p-4">
                <label htmlFor="media-transcript-file" className="block text-ui-sm font-medium">人工转写</label>
                <Input ref={transcriptInputRef} id="media-transcript-file" type="file" accept=".md,text/markdown" disabled={uploading}
                  onChange={(event) => setTranscriptFile(event.target.files?.[0] || null)} className="mt-3 h-auto min-h-control-md py-1.5" />
                <p className="mt-2 text-ui-xs text-muted-foreground">{transcriptFile ? `${transcriptFile.name} · ${formatBytes(transcriptFile.size)}` : "尚未选择转写文件"}</p>
              </div>
            ) : (
              <div className="rounded-ui-xl border border-border bg-surface-muted/40 p-4">
                <label htmlFor="transcription-profile" className="block text-ui-sm font-medium">转录 Profile</label>
                <select id="transcription-profile" value={profileId} disabled={uploading} onChange={(event) => { setProfileId(event.target.value); rotateRequestIdentity(); }}
                  className="mt-3 h-control-md w-full rounded-ui-md border border-input bg-background px-3 text-ui-sm">
                  <option value="">请选择服务端 Profile</option>
                  {profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id} disabled={profile.admission !== "enabled" || profile.availability !== "available"}>{profile.display_name}{profile.availability !== "available" ? "（不可用）" : ""}</option>)}
                </select>
                <p className="mt-2 text-ui-xs text-muted-foreground">{selectedProfile?.description || "仅可选择服务端白名单中的可用 Profile。"}</p>
              </div>
            )}
          </div>
          {uploadError && <Alert variant="destructive" role="alert"><AlertTitle>视频上传失败</AlertTitle><AlertDescription>{uploadError}</AlertDescription></Alert>}
          <div className="flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-ui-xs text-muted-foreground">{mode === "manual" ? "人工 Markdown 路径保持不变。" : "自动任务会生成待审核草稿。"}</p>
            <Button onClick={handleUpload} disabled={!canUpload}>{uploading ? "正在提交…" : mode === "manual" ? "上传视频与人工转写" : "上传并开始自动转录"}</Button>
          </div>
        </CardContent>
      </Card>

      <section className="space-y-3" aria-labelledby="media-assets-title">
        <div className="flex items-end justify-between"><div><h2 id="media-assets-title" className="text-ui-base font-semibold">媒体资源</h2><p className="mt-1 text-ui-xs text-muted-foreground">查看媒体与最新转录任务状态。</p></div><span className="text-ui-xs text-muted-foreground">共 {mediaAssets.length} 个视频</span></div>
        {jobsError && <Alert role="alert"><AlertTitle>任务状态暂时无法刷新</AlertTitle><AlertDescription>{jobsError}</AlertDescription></Alert>}
        {loadError ? <ErrorState title="媒体资源加载失败" description={loadError} action={<Button variant="outline" size="sm" onClick={refresh}>重新加载</Button>} />
          : loading ? <Card><LoadingState className="min-h-48" label="正在加载媒体资源…" /></Card>
          : mediaAssets.length === 0 ? <EmptyState title="暂无媒体资源" description="上传第一个视频及其人工转写后，处理状态会显示在这里。" />
          : <Card className="overflow-hidden shadow-surface"><div className="overflow-x-auto"><table className="w-full min-w-[60rem] text-ui-sm"><caption className="sr-only">视频媒体和最新转录任务</caption><thead className="border-b border-border bg-surface-muted"><tr><th className="px-4 py-3 text-left">标题</th><th className="px-4 py-3 text-left">原始文件</th><th className="px-4 py-3 text-right">大小</th><th className="px-4 py-3 text-left">媒体状态</th><th className="px-4 py-3 text-left">转录任务</th><th className="px-4 py-3 text-left">创建时间</th></tr></thead><tbody className="divide-y divide-border">
            {mediaAssets.map((asset) => { const job = jobsByMediaId.get(asset.media_id); return <tr key={asset.media_id}><td className="px-4 py-3 font-medium">{asset.title}</td><td className="px-4 py-3 font-mono text-ui-xs text-muted-foreground">{asset.original_filename}</td><td className="px-4 py-3 text-right">{formatBytes(asset.file_size)}</td><td className="px-4 py-3"><MediaStatusBadge status={asset.status} />{asset.error && <p className="mt-1 text-ui-xs text-destructive">{asset.error}</p>}</td><td className="px-4 py-3">{job ? <><Badge variant={job.status === "failed" ? "destructive" : job.status === "succeeded" ? "success" : "secondary"}>{job.status}</Badge><JobSummary job={job} /><div className="mt-2 flex gap-2">{(job.status === "pending" || job.status === "running") && <Button size="sm" variant="outline" onClick={() => void cancelJob(job)}>取消</Button>}{(job.status === "failed" || job.status === "cancelled") && <Button size="sm" variant="outline" onClick={() => void retryJob(job)}>重试</Button>}</div></> : <span className="text-ui-xs text-muted-foreground">人工转写或暂无任务</span>}</td><td className="px-4 py-3 text-ui-xs text-muted-foreground">{formatAdminDate(asset.created_at)}</td></tr>; })}
          </tbody></table></div></Card>}
      </section>
    </section>
  );
}
