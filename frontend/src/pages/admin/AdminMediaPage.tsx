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
import type { MediaAsset } from "../../types";
import { formatAdminDate, formatBytes } from "./admin-formatters";

type StatusVariant = "secondary" | "success" | "warning" | "destructive" | "info";

const mediaStatusMeta: Record<string, { label: string; variant: StatusVariant }> = {
  uploading: { label: "上传中", variant: "info" },
  transcript_ready: { label: "转写就绪", variant: "info" },
  indexing: { label: "索引中", variant: "warning" },
  ready: { label: "已就绪", variant: "success" },
  failed: { label: "失败", variant: "destructive" },
};

function MediaStatusBadge({ status }: { status: string }) {
  const meta = mediaStatusMeta[status];
  return <Badge variant={meta?.variant ?? "secondary"}>{meta?.label ?? status}</Badge>;
}

export function AdminMediaPage() {
  const [mediaAssets, setMediaAssets] = useState<MediaAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [transcriptFile, setTranscriptFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const videoInputRef = useRef<HTMLInputElement>(null);
  const transcriptInputRef = useRef<HTMLInputElement>(null);

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

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleUpload() {
    if (!videoFile || !transcriptFile || !title.trim()) return;

    setUploading(true);
    setUploadError(null);
    try {
      await api.uploadMediaVideo(videoFile, transcriptFile, title.trim());
      setVideoFile(null);
      setTranscriptFile(null);
      setTitle("");
      if (videoInputRef.current) videoInputRef.current.value = "";
      if (transcriptInputRef.current) transcriptInputRef.current.value = "";
      await refresh();
    } catch (e: any) {
      setUploadError(e?.message || String(e));
    } finally {
      setUploading(false);
    }
  }

  const canUpload = Boolean(videoFile && transcriptFile && title.trim() && !uploading);

  return (
    <section className="space-y-6" aria-labelledby="admin-media-title">
      <header>
        <p className="text-ui-xs font-medium uppercase tracking-[0.14em] text-primary">媒体与转写</p>
        <h1 id="admin-media-title" className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">
          视频媒体
        </h1>
        <p className="mt-1 max-w-3xl text-ui-sm text-muted-foreground">
          上传培训视频及对应的人工转写文件，并查看媒体处理和索引状态。
        </p>
      </header>

      <Card className="shadow-surface">
        <CardHeader className="p-5 pb-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <CardTitle id="media-upload-title" className="text-ui-lg">
                上传视频与转写
              </CardTitle>
              <CardDescription className="mt-1 max-w-3xl leading-relaxed">
                视频仅支持 MP4；请同时提供 Markdown 人工转写。每行以“说话人 HH:MM:SS”开头，上传后系统会自动建立索引。
              </CardDescription>
            </div>
            <Badge variant="outline" className="shrink-0">
              MP4 + Markdown
            </Badge>
          </div>
        </CardHeader>

        <CardContent className="space-y-5 px-5 pb-5 pt-0">
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="lg:col-span-2">
              <label htmlFor="media-title" className="mb-1.5 block text-ui-sm font-medium text-foreground">
                视频标题
              </label>
              <Input
                id="media-title"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="例如：2024 年项目培训视频"
                disabled={uploading}
              />
            </div>

            <div className="rounded-ui-xl border border-border bg-surface-muted/40 p-4">
              <label htmlFor="media-video-file" className="block text-ui-sm font-medium text-foreground">
                视频文件
              </label>
              <p className="mt-1 text-ui-xs text-muted-foreground">选择一个 MP4 视频文件。</p>
              <Input
                ref={videoInputRef}
                id="media-video-file"
                type="file"
                accept=".mp4,video/mp4"
                disabled={uploading}
                onChange={(event) => setVideoFile(event.target.files?.[0] || null)}
                className="mt-3 h-auto min-h-control-md cursor-pointer py-1.5 file:mr-3 file:rounded-ui-sm file:border-0 file:bg-secondary file:px-3 file:py-1.5 file:text-ui-xs file:font-medium file:text-secondary-foreground hover:file:bg-secondary/80"
              />
              <p className="mt-2 min-h-5 text-ui-xs text-muted-foreground" aria-live="polite">
                {videoFile ? `${videoFile.name} · ${formatBytes(videoFile.size)}` : "尚未选择视频文件"}
              </p>
            </div>

            <div className="rounded-ui-xl border border-border bg-surface-muted/40 p-4">
              <label htmlFor="media-transcript-file" className="block text-ui-sm font-medium text-foreground">
                人工转写
              </label>
              <p className="mt-1 text-ui-xs text-muted-foreground">选择与视频对应的 Markdown 文件。</p>
              <Input
                ref={transcriptInputRef}
                id="media-transcript-file"
                type="file"
                accept=".md,text/markdown"
                disabled={uploading}
                onChange={(event) => setTranscriptFile(event.target.files?.[0] || null)}
                className="mt-3 h-auto min-h-control-md cursor-pointer py-1.5 file:mr-3 file:rounded-ui-sm file:border-0 file:bg-secondary file:px-3 file:py-1.5 file:text-ui-xs file:font-medium file:text-secondary-foreground hover:file:bg-secondary/80"
              />
              <p className="mt-2 min-h-5 text-ui-xs text-muted-foreground" aria-live="polite">
                {transcriptFile
                  ? `${transcriptFile.name} · ${formatBytes(transcriptFile.size)}`
                  : "尚未选择转写文件"}
              </p>
            </div>
          </div>

          {uploadError && (
            <Alert variant="destructive" role="alert">
              <AlertTitle>视频上传失败</AlertTitle>
              <AlertDescription>{uploadError}</AlertDescription>
            </Alert>
          )}

          <div className="flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-ui-xs text-muted-foreground">标题、视频文件和转写文件均准备完成后才能开始上传。</p>
            <Button onClick={handleUpload} disabled={!canUpload} className="w-full sm:w-auto">
              {uploading ? "正在上传并建立索引…" : "上传并建立索引"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <section className="space-y-3" aria-labelledby="media-assets-title">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 id="media-assets-title" className="text-ui-base font-semibold text-foreground">
              媒体资源
            </h2>
            <p className="mt-1 text-ui-xs text-muted-foreground">查看视频文件及其转写、索引处理状态。</p>
          </div>
          <span className="text-ui-xs text-muted-foreground" aria-live="polite">
            共 {mediaAssets.length} 个视频
          </span>
        </div>

        {loadError ? (
          <ErrorState
            title="媒体资源加载失败"
            description={loadError}
            action={
              <Button variant="outline" size="sm" onClick={refresh}>
                重新加载
              </Button>
            }
          />
        ) : loading ? (
          <Card>
            <LoadingState className="min-h-48" label="正在加载媒体资源…" />
          </Card>
        ) : mediaAssets.length === 0 ? (
          <EmptyState
            title="暂无媒体资源"
            description="上传第一个视频及其人工转写后，处理状态会显示在这里。"
          />
        ) : (
          <Card className="overflow-hidden shadow-surface">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[52rem] text-ui-sm">
                <caption className="sr-only">视频媒体文件、大小、处理状态和创建时间</caption>
                <thead className="border-b border-border bg-surface-muted text-muted-foreground">
                  <tr>
                    <th scope="col" className="px-4 py-3 text-left font-medium">标题</th>
                    <th scope="col" className="px-4 py-3 text-left font-medium">原始文件</th>
                    <th scope="col" className="px-4 py-3 text-right font-medium">大小</th>
                    <th scope="col" className="px-4 py-3 text-left font-medium">状态</th>
                    <th scope="col" className="px-4 py-3 text-left font-medium">创建时间</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {mediaAssets.map((asset) => (
                    <tr
                      key={asset.media_id}
                      className="bg-card transition-colors duration-normal hover:bg-surface-muted/60"
                    >
                      <td className="px-4 py-3">
                        <div className="max-w-xs truncate font-medium text-foreground" title={asset.title}>
                          {asset.title}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <div
                          className="max-w-xs truncate font-mono text-ui-xs text-muted-foreground"
                          title={asset.original_filename}
                        >
                          {asset.original_filename}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                        {formatBytes(asset.file_size)}
                      </td>
                      <td className="px-4 py-3">
                        <MediaStatusBadge status={asset.status} />
                        {asset.error && (
                          <p className="mt-1 max-w-sm text-ui-xs leading-relaxed text-destructive">{asset.error}</p>
                        )}
                      </td>
                      <td className="px-4 py-3 text-ui-xs text-muted-foreground">
                        {formatAdminDate(asset.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </section>
    </section>
  );
}
