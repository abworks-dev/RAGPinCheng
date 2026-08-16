import { useCallback, useEffect, useMemo, useState } from "react";
import { adminMediaApi } from "../api/admin/media";
import { useTranscriptPublicationJob } from "../hooks/useTranscriptionJobs";
import type { MediaTranscript, TranscriptVersion } from "../types";
import { Alert, AlertDescription, AlertTitle } from "./ui/alert";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { TranscriptMarkdownEditor } from "./TranscriptMarkdownEditor";
import { SynchronizedVideoTranscript } from "./SynchronizedVideoTranscript";

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

function sourceLabel(version: TranscriptVersion) {
  if (version.source === "automatic") return "自动转录";
  if (version.source === "manual" && version.derived_from_version_id) return "人工修订";
  if (version.source === "manual") return "人工转录";
  return "其他来源";
}

function newIdempotencyKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (character) => {
    const random = Math.floor(Math.random() * 16);
    return (character === "x" ? random : (random & 0x3) | 0x8).toString(16);
  });
}

type EditorState = {
  baseVersionId: string;
  baseMarkdownSha256: string;
  markdown: string;
  savedMarkdown: string;
  requestIdempotencyKey: string;
};

export function TranscriptionVersionPanel({ mediaId, refreshToken, embedded = false, onChanged, onDirtyChange }: { mediaId: string; refreshToken?: string | null; embedded?: boolean; onChanged?: () => void | Promise<void>; onDirtyChange?: (dirty: boolean) => void }) {
  const [expanded, setExpanded] = useState(embedded);
  const [versions, setVersions] = useState<TranscriptVersion[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [timeline, setTimeline] = useState<MediaTranscript | null>(null);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState<string | null>(null);
  const [editorMode, setEditorMode] = useState<"edit" | "preview">("edit");
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);
  const [reviewNote, setReviewNote] = useState<Record<string, string>>({});
  const [busyVersionId, setBusyVersionId] = useState<string | null>(null);
  const [publicationJobId, setPublicationJobId] = useState<string | null>(null);
  const { job: publicationJob, error: publicationError } = useTranscriptPublicationJob(publicationJobId);
  const editorDirty = editor !== null && editor.markdown !== editor.savedMarkdown;

  useEffect(() => onDirtyChange?.(editorDirty), [editorDirty, onDirtyChange]);

  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

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
    setEditor(null);
    setTimeline(null);
    setTimelineError(null);
    setSaveSuccess(null);
  }, [mediaId]);

  useEffect(() => {
    if (publicationJob?.status === "done" || publicationJob?.status === "failed") void loadVersions();
  }, [publicationJob?.status, loadVersions]);

  const currentVersionId = useMemo(() => versions.find((version) => version.is_current)?.version_id ?? null, [versions]);

  const previewVersion = async (versionId: string) => {
    if (editor?.baseVersionId === versionId) return;
    if (editorDirty && !window.confirm("当前修改尚未保存，确定切换到其他版本吗？")) return;
    setBusyVersionId(versionId);
    setTimelineLoading(true);
    setTimelineError(null);
    try {
      const result = await adminMediaApi.previewVersion(versionId);
      setEditor({
        baseVersionId: versionId,
        baseMarkdownSha256: result.markdown_sha256,
        markdown: result.markdown,
        savedMarkdown: result.markdown,
        requestIdempotencyKey: newIdempotencyKey(),
      });
      setEditorMode("edit");
      setSaveSuccess(null);
      setError(null);
      try {
        setTimeline(await adminMediaApi.previewVersionTimeline(versionId));
      } catch (caught: any) {
        setTimeline(null);
        setTimelineError(caught?.message || "暂时无法加载视频时间轴");
      }
    } catch (caught: any) {
      setError(caught?.message || String(caught));
    } finally {
      setTimelineLoading(false);
      setBusyVersionId(null);
    }
  };

  const saveRevision = async () => {
    if (!editor || !editorDirty) return;
    setBusyVersionId(editor.baseVersionId);
    try {
      const saved = await adminMediaApi.createRevision(
        editor.baseVersionId,
        editor.markdown,
        editor.baseMarkdownSha256,
        editor.requestIdempotencyKey,
      );
      await loadVersions();
      setEditor({
        baseVersionId: saved.version_id,
        baseMarkdownSha256: saved.markdown_sha256,
        markdown: editor.markdown.replace(/\r\n?/g, "\n"),
        savedMarkdown: editor.markdown.replace(/\r\n?/g, "\n"),
        requestIdempotencyKey: newIdempotencyKey(),
      });
      try {
        setTimeline(await adminMediaApi.previewVersionTimeline(saved.version_id));
        setTimelineError(null);
      } catch (caught: any) {
        setTimeline(null);
        setTimelineError(caught?.message || "草稿已保存，但视频时间轴加载失败");
      }
      setSaveSuccess("新草稿已保存，审核状态已重置为待审核。");
      await onChanged?.();
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
      {!embedded && <Button size="sm" variant="outline" onClick={() => {
        if (expanded && editorDirty && !window.confirm("当前修改尚未保存，确定收起吗？")) return;
        setExpanded((value) => !value);
      }}>
        {expanded ? "收起转录版本" : "审阅转录版本"}
      </Button>}
      {expanded && (
        <div className="min-w-0 space-y-3">
          {(error || publicationError) && <Alert variant="destructive" role="alert"><AlertTitle>转录版本操作失败</AlertTitle><AlertDescription>{error || publicationError}</AlertDescription></Alert>}
          {saveSuccess && <Alert role="status"><AlertTitle>草稿已保存</AlertTitle><AlertDescription>{saveSuccess}</AlertDescription></Alert>}
          {publicationJob && <p className="text-ui-xs text-muted-foreground">候选索引：{statusLabel(publicationJob.status)}{publicationJob.error_summary ? ` · ${publicationJob.error_summary}` : ""}</p>}
          {loading && versions.length === 0 ? <p className="text-ui-xs text-muted-foreground">正在加载转录版本…</p> : null}
          {!loading && versions.length === 0 ? <p className="text-ui-xs text-muted-foreground">暂无可审阅转录版本。</p> : null}
          {versions.map((version) => {
            const busy = busyVersionId === version.version_id;
            const isEditing = editor?.baseVersionId === version.version_id;
            const managedManualRevision = version.source === "manual" && version.markdown_storage_kind === "managed_artifact" && Boolean(version.derived_from_version_id);
            const canPublish = (version.source === "automatic" || managedManualRevision) && version.review_status === "review_approved" && (version.publication_status === "not_published" || version.publication_status === "publication_failed");
            const publishLabel = version.publication_status === "published"
              ? "已发布"
              : version.publication_status === "publishing"
                ? "发布中"
                : "发布到知识库";
            const publishHint = version.publication_status === "published"
              ? "当前版本已发布"
              : version.publication_status === "publishing"
                ? "正在处理发布任务"
                : version.source !== "automatic" && !managedManualRevision
                  ? "旧版人工转录稿不能通过受管流程发布"
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
                  <div><dt className="inline font-medium text-foreground">来源：</dt> <dd className="inline">{sourceLabel(version)}</dd></div>
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
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      aria-expanded={isEditing}
                      onClick={() => {
                        if (!isEditing) {
                          void previewVersion(version.version_id);
                          return;
                        }
                        if (editorDirty && !window.confirm("当前修改尚未保存，确定收起校对内容吗？")) return;
                        setEditor(null);
                        setTimeline(null);
                        setTimelineError(null);
                        setSaveSuccess(null);
                      }}
                    >
                      {isEditing ? "收起校对" : "校对内容"}
                    </Button>
                  </div>
                  {isEditing && editor && (
                    <>
                      <section className="mt-3 space-y-2" aria-label="视频时间轴校对">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <h5 className="text-ui-xs font-medium text-foreground">视频校对</h5>
                          {timelineError && <span className="text-ui-xs text-destructive">{timelineError}</span>}
                        </div>
                        <SynchronizedVideoTranscript
                          mediaId={mediaId}
                          segments={timeline?.segments ?? []}
                          transcriptLoading={timelineLoading}
                          transcriptError={timelineError}
                          layout="split"
                        />
                      </section>
                      <TranscriptMarkdownEditor
                        value={editor.markdown}
                        onChange={(markdown) => {
                          setEditor((current) => current ? {
                            ...current,
                            markdown,
                            requestIdempotencyKey: current.markdown === markdown
                              ? current.requestIdempotencyKey
                              : newIdempotencyKey(),
                          } : current);
                          setSaveSuccess(null);
                        }}
                        mode={editorMode}
                        onModeChange={setEditorMode}
                        disabled={busy}
                        onSave={() => void saveRevision()}
                        dirty={editorDirty}
                      />
                    </>
                  )}
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
