import { useCallback, useEffect, useMemo, useState } from "react";
import { adminMediaApi } from "../api/admin/media";
import { useTranscriptPublicationJob } from "../hooks/useTranscriptionJobs";
import type { TranscriptVersion } from "../types";
import { Alert, AlertDescription, AlertTitle } from "./ui/alert";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

function statusLabel(status: string) {
  return ({
    awaiting_review: "待审核",
    review_approved: "审核通过",
    review_rejected: "审核拒绝",
    not_required: "无需审核",
    draft: "草稿",
    not_published: "未发布",
    publishing: "发布中",
    published: "已发布",
    publication_failed: "发布失败",
    pending: "等待索引",
    parsing: "解析中",
    chunking: "分块中",
    embedding: "向量化中",
    done: "候选索引完成",
    failed: "候选索引失败",
  } as Record<string, string>)[status] ?? status;
}

function sourceLabel(source: string) {
  return ({ automatic: "自动转录", manual: "人工转录" } as Record<string, string>)[source] ?? "其他来源";
}

export function TranscriptionVersionPanel({ mediaId, refreshToken, embedded = false, onChanged }: { mediaId: string; refreshToken?: string | null; embedded?: boolean; onChanged?: () => void | Promise<void> }) {
  const [expanded, setExpanded] = useState(embedded);
  const [versions, setVersions] = useState<TranscriptVersion[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ versionId: string; markdown: string } | null>(null);
  const [reviewNote, setReviewNote] = useState<Record<string, string>>({});
  const [busyVersionId, setBusyVersionId] = useState<string | null>(null);
  const [publicationJobId, setPublicationJobId] = useState<string | null>(null);
  const { job: publicationJob, error: publicationError } = useTranscriptPublicationJob(publicationJobId);

  const loadVersions = useCallback(async () => {
    setLoading(true);
    try {
      setVersions(await adminMediaApi.versions(mediaId));
      setError(null);
    } catch (caught: any) {
      setError(caught?.message || String(caught));
    } finally {
      setLoading(false);
    }
  }, [mediaId]);

  useEffect(() => {
    if (expanded) void loadVersions();
  }, [expanded, loadVersions, refreshToken]);

  useEffect(() => {
    if (publicationJob?.status === "done" || publicationJob?.status === "failed") void loadVersions();
  }, [publicationJob?.status, loadVersions]);

  const currentVersionId = useMemo(() => versions.find((version) => version.is_current)?.version_id ?? null, [versions]);

  const previewVersion = async (versionId: string) => {
    setBusyVersionId(versionId);
    try {
      const result = await adminMediaApi.previewVersion(versionId);
      setPreview({ versionId, markdown: result.markdown });
      setError(null);
    } catch (caught: any) {
      setError(caught?.message || String(caught));
    } finally {
      setBusyVersionId(null);
    }
  };

  const reviewVersion = async (versionId: string, approved: boolean) => {
    setBusyVersionId(versionId);
    try {
      await adminMediaApi.reviewVersion(versionId, approved, reviewNote[versionId]?.trim() || null);
      await loadVersions();
      await onChanged?.();
    } catch (caught: any) {
      setError(caught?.message || String(caught));
    } finally {
      setBusyVersionId(null);
    }
  };

  const publishVersion = async (versionId: string) => {
    setBusyVersionId(versionId);
    try {
      const result = await adminMediaApi.publishVersion(versionId);
      setPublicationJobId(result.job?.index_job_id ?? null);
      await loadVersions();
      await onChanged?.();
    } catch (caught: any) {
      setError(caught?.message || String(caught));
    } finally {
      setBusyVersionId(null);
    }
  };

  return (
    <div className={embedded ? "space-y-3" : "mt-3 border-t border-border pt-3"}>
      {!embedded && <Button size="sm" variant="outline" onClick={() => setExpanded((value) => !value)}>
        {expanded ? "收起转录版本" : "审阅转录版本"}
      </Button>}
      {expanded && (
        <div className="min-w-0 space-y-3">
          {(error || publicationError) && <Alert variant="destructive" role="alert"><AlertTitle>转录版本操作失败</AlertTitle><AlertDescription>{error || publicationError}</AlertDescription></Alert>}
          {publicationJob && <p className="text-ui-xs text-muted-foreground">候选索引：{statusLabel(publicationJob.status)}{publicationJob.error_summary ? ` · ${publicationJob.error_summary}` : ""}</p>}
          {loading && versions.length === 0 ? <p className="text-ui-xs text-muted-foreground">正在加载转录版本…</p> : null}
          {!loading && versions.length === 0 ? <p className="text-ui-xs text-muted-foreground">暂无可审阅转录版本。</p> : null}
          {versions.map((version) => {
            const busy = busyVersionId === version.version_id;
            const canPublish = version.source === "automatic" && version.review_status === "review_approved" && (version.publication_status === "not_published" || version.publication_status === "publication_failed");
            const publishLabel = version.publication_status === "published"
              ? "已发布"
              : version.publication_status === "publishing"
                ? "发布中"
                : "发布到知识库";
            const publishHint = version.publication_status === "published"
              ? "当前版本已发布"
              : version.publication_status === "publishing"
                ? "正在处理发布任务"
                : version.source !== "automatic"
                  ? "人工转录版本暂不支持自动发布"
                  : version.review_status !== "review_approved"
                    ? "审核通过后可发布"
                    : null;
            const publishHintId = `publish-hint-${version.version_id}`;
            return (
              <article key={version.version_id} className="rounded-ui-md border border-border bg-background p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={version.is_current ? "success" : "secondary"}>{version.is_current ? "当前正式版本" : statusLabel(version.publication_status)}</Badge>
                  <Badge variant="secondary">{statusLabel(version.review_status)}</Badge>
                </div>
                <dl className="mt-3 grid gap-2 text-ui-xs text-muted-foreground sm:grid-cols-2">
                  <div><dt className="inline font-medium text-foreground">来源：</dt> <dd className="inline">{sourceLabel(version.source)}</dd></div>
                  <div><dt className="inline font-medium text-foreground">审核状态：</dt> <dd className="inline">{statusLabel(version.review_status)}</dd></div>
                </dl>
                <details className="mt-3 text-ui-xs text-muted-foreground">
                  <summary className="cursor-pointer select-none font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">查看技术详情</summary>
                  <dl className="mt-2 grid gap-1 rounded-ui-sm bg-surface-muted/60 p-3 sm:grid-cols-2">
                    <div><dt className="inline">版本编号：</dt> <dd className="inline break-all font-mono">{version.version_id}</dd></div>
                    <div><dt className="inline">Profile 标识：</dt> <dd className="inline break-all font-mono">{version.profile_id || "—"}</dd></div>
                    <div><dt className="inline">Provider 标识：</dt> <dd className="inline break-all font-mono">{version.provider_key || "—"}</dd></div>
                    <div><dt className="inline">模型：</dt> <dd className="inline break-all font-mono">{version.model_id ? `${version.model_id}@${version.model_revision || "—"}` : "—"}</dd></div>
                  </dl>
                </details>
                {version.review_note && <p className="mt-1 text-ui-xs">审核备注：{version.review_note}</p>}
                <section className="mt-4 border-t border-border pt-4" aria-labelledby={`transcript-content-${version.version_id}`}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h4 id={`transcript-content-${version.version_id}`} className="text-ui-sm font-semibold">转录内容</h4>
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => void previewVersion(version.version_id)}>预览 Markdown</Button>
                  </div>
                  {preview?.versionId === version.version_id && <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap rounded-ui-md bg-surface-muted p-3 text-ui-xs">{preview.markdown}</pre>}
                </section>
                <section className="mt-4 border-t border-border pt-4" aria-labelledby={`transcript-actions-${version.version_id}`}>
                  <h4 id={`transcript-actions-${version.version_id}`} className="text-ui-sm font-semibold">审核与发布</h4>
                  {version.review_status === "awaiting_review" && <div className="mt-3">
                    <label htmlFor={`review-note-${version.version_id}`} className="text-ui-xs font-medium">审核备注 <span className="font-normal text-muted-foreground">（可选）</span></label>
                    <Input id={`review-note-${version.version_id}`} aria-label={`审核备注 ${version.version_id}`} className="mt-1 h-9" placeholder="记录术语、时间轴或内容判断依据" value={reviewNote[version.version_id] || ""} onChange={(event) => setReviewNote((current) => ({ ...current, [version.version_id]: event.target.value }))} />
                  </div>}
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    {version.review_status === "awaiting_review" && <>
                      <Button size="sm" disabled={busy} onClick={() => void reviewVersion(version.version_id, true)}>审核通过</Button>
                      <Button size="sm" variant="outline" disabled={busy} onClick={() => void reviewVersion(version.version_id, false)}>拒绝</Button>
                    </>}
                    <div className="flex flex-wrap items-center gap-2 sm:ml-auto">
                      <Button size="sm" disabled={busy || !canPublish} aria-describedby={!canPublish ? publishHintId : undefined} onClick={() => void publishVersion(version.version_id)}>{publishLabel}</Button>
                      {publishHint && <span id={publishHintId} className="text-ui-xs text-muted-foreground">{publishHint}</span>}
                    </div>
                  </div>
                </section>
              </article>
            );
          })}
          {currentVersionId && <p className="text-ui-xs text-muted-foreground">正式检索 head：{currentVersionId}</p>}
        </div>
      )}
    </div>
  );
}
