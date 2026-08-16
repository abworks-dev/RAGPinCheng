import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
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
      setVersions(await api.listTranscriptVersions(mediaId));
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
      const result = await api.previewTranscriptVersion(versionId);
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
      await api.reviewTranscriptVersion(versionId, approved, reviewNote[versionId]?.trim() || null);
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
      const result = await api.publishTranscriptVersion(versionId);
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
            return (
              <div key={version.version_id} className="rounded-ui-md border border-border bg-background p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={version.is_current ? "success" : "secondary"}>{version.is_current ? "当前正式版本" : statusLabel(version.publication_status)}</Badge>
                  <Badge variant="secondary">{statusLabel(version.review_status)}</Badge>
                  <span className="font-mono text-ui-xs text-muted-foreground">{version.version_id}</span>
                </div>
                <p className="mt-2 break-words text-ui-xs text-muted-foreground">
                  来源 {version.source} · Profile {version.profile_id || "人工"} · Provider {version.provider_key || "—"} · 模型 {version.model_id || "—"}@{version.model_revision || "—"}
                </p>
                {version.review_note && <p className="mt-1 text-ui-xs">审核备注：{version.review_note}</p>}
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button size="sm" variant="outline" disabled={busy} onClick={() => void previewVersion(version.version_id)}>预览 Markdown</Button>
                  {version.review_status === "awaiting_review" && <>
                    <Input aria-label={`审核备注 ${version.version_id}`} className="h-8 min-w-0 flex-1 sm:w-48 sm:flex-none" value={reviewNote[version.version_id] || ""} onChange={(event) => setReviewNote((current) => ({ ...current, [version.version_id]: event.target.value }))} />
                    <Button size="sm" disabled={busy} onClick={() => void reviewVersion(version.version_id, true)}>审核通过</Button>
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => void reviewVersion(version.version_id, false)}>拒绝</Button>
                  </>}
                  <Button size="sm" disabled={busy || !canPublish} onClick={() => void publishVersion(version.version_id)}>发布</Button>
                </div>
                {preview?.versionId === version.version_id && <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap rounded-ui-md bg-surface-muted p-3 text-ui-xs">{preview.markdown}</pre>}
              </div>
            );
          })}
          {currentVersionId && <p className="text-ui-xs text-muted-foreground">正式检索 head：{currentVersionId}</p>}
        </div>
      )}
    </div>
  );
}
