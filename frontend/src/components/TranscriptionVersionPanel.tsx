import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, Trash2 } from "lucide-react";
import { adminMediaApi } from "../api/admin/media";
import { useTranscriptPublicationJob } from "../hooks/useTranscriptionJobs";
import type { MediaTranscript, TranscriptVersion, TranscriptVersionBulkDeleteResult } from "../types";
import { Alert, AlertDescription, AlertTitle } from "./ui/alert";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "./ui/dialog";
import { IconButton } from "./ui/icon-button";
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

function formatVersionTime(timestamp: number) {
  const date = new Date(timestamp * 1000);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return date.toLocaleString("zh-CN", { hour12: false });
}

function newIdempotencyKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (character) => {
    const random = Math.floor(Math.random() * 16);
    return (character === "x" ? random : (random & 0x3) | 0x8).toString(16);
  });
}

/**
 * Client-side mirror of the permanent-delete rules in the approved plan
 * (`docs/plans/transcript-version-batch-delete.md`): a version is deletable only
 * when it is not the current head, never published, not awaiting review, and not
 * referenced by another version. The backend stays the authority; this only
 * explains the rule to the user before they click.
 */
function deleteBlockReason(version: TranscriptVersion, referenced: boolean): string | null {
  if (version.is_current) return "当前正式检索版本，不能删除";
  if (referenced) return "已被其他版本引用，不能删除";
  if (version.publication_status === "published") return "已发布到知识库，不能删除";
  if (version.publication_status === "publishing") return "正在发布，暂时不能删除";
  if (version.publication_status === "publication_failed") return "发布失败版本需保留排查，不能删除";
  if (version.review_status === "awaiting_review") return "正在等待审核，不能删除";
  // Legacy hand-written transcripts live outside the managed artifact root and are
  // never removed by this flow.
  if (version.markdown_storage_kind !== "managed_artifact") return "早期人工转录稿由系统归档，不能删除";
  return null;
}

type EditorState = {
  baseVersionId: string;
  baseMarkdownSha256: string;
  markdown: string;
  savedMarkdown: string;
  requestIdempotencyKey: string;
};

export function TranscriptionVersionPanel({ mediaId, refreshToken, embedded = false, initialAction = null, initialVersionId = null, onChanged, onDirtyChange }: { mediaId: string; refreshToken?: string | null; embedded?: boolean; initialAction?: "edit-current" | null; initialVersionId?: string | null; onChanged?: () => void | Promise<void>; onDirtyChange?: (dirty: boolean) => void }) {
  const [expanded, setExpanded] = useState(embedded);
  const [versions, setVersions] = useState<TranscriptVersion[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<MediaTranscript | null>(null);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState<string | null>(null);
  const [editorMode, setEditorMode] = useState<"edit" | "preview">("edit");
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);
  const [reviewNote, setReviewNote] = useState<Record<string, string>>({});
  const [busyVersionId, setBusyVersionId] = useState<string | null>(null);
  const [publicationJobId, setPublicationJobId] = useState<string | null>(null);
  const [batchMode, setBatchMode] = useState(false);
  const [batchSelectedIds, setBatchSelectedIds] = useState<string[]>([]);
  const [batchConfirmOpen, setBatchConfirmOpen] = useState(false);
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchRequestKey, setBatchRequestKey] = useState<string | null>(null);
  const [batchConfirmLabels, setBatchConfirmLabels] = useState<Record<string, string>>({});
  const [batchError, setBatchError] = useState<string | null>(null);
  const [batchResult, setBatchResult] = useState<TranscriptVersionBulkDeleteResult | null>(null);
  // Open by default: a closed <details> hides its content in every viewport,
  // including the `lg:contents` desktop layout, so the rail would disappear.
  const [listOpen, setListOpen] = useState(true);
  const { job: publicationJob, error: publicationError } = useTranscriptPublicationJob(publicationJobId);
  const initialOpenKeyRef = useRef<string | null>(null);
  const rowRefs = useRef<Record<string, HTMLElement | null>>({});
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
    setSelectedVersionId(null);
    setTimeline(null);
    setTimelineError(null);
    setSaveSuccess(null);
    setBatchMode(false);
    setBatchSelectedIds([]);
    setBatchResult(null);
    setBatchError(null);
    setBatchConfirmOpen(false);
    initialOpenKeyRef.current = null;
  }, [mediaId]);

  useEffect(() => {
    if (publicationJob?.status === "done" || publicationJob?.status === "failed") void loadVersions();
  }, [publicationJob?.status, loadVersions]);

  const referencedVersionIds = useMemo(() => {
    const referenced = new Set<string>();
    for (const version of versions) {
      if (version.supersedes_version_id) referenced.add(version.supersedes_version_id);
      if (version.derived_from_version_id) referenced.add(version.derived_from_version_id);
    }
    return referenced;
  }, [versions]);

  const blockReasons = useMemo(() => {
    const reasons: Record<string, string | null> = {};
    for (const version of versions) {
      reasons[version.version_id] = deleteBlockReason(version, referencedVersionIds.has(version.version_id));
    }
    return reasons;
  }, [referencedVersionIds, versions]);

  const deletableVersionIds = useMemo(
    () => versions.filter((version) => !blockReasons[version.version_id]).map((version) => version.version_id),
    [blockReasons, versions],
  );

  const batchSelectedSet = useMemo(() => new Set(batchSelectedIds), [batchSelectedIds]);
  const batchSelectedVersions = useMemo(
    () => versions.filter((version) => batchSelectedSet.has(version.version_id)),
    [batchSelectedSet, versions],
  );
  const batchResultByVersionId = useMemo(() => {
    const map = new Map<string, TranscriptVersionBulkDeleteResult["items"][number]>();
    for (const item of batchResult?.items ?? []) map.set(item.version_id, item);
    return map;
  }, [batchResult]);

  const selectedIndex = useMemo(
    () => (selectedVersionId ? versions.findIndex((version) => version.version_id === selectedVersionId) : -1),
    [selectedVersionId, versions],
  );
  const hasPreviousVersion = selectedIndex > 0;
  const hasNextVersion = selectedIndex >= 0 && selectedIndex < versions.length - 1;

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
      setSelectedVersionId(versionId);
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

  useEffect(() => {
    if (!expanded || loading || versions.length === 0) return;
    const requestedVersionId = initialVersionId
      || (initialAction === "edit-current" ? versions.find((version) => version.is_current)?.version_id : null);
    if (!requestedVersionId) return;
    const key = `${mediaId}:${requestedVersionId}`;
    if (initialOpenKeyRef.current === key) return;
    if (!versions.some((version) => version.version_id === requestedVersionId)) return;
    initialOpenKeyRef.current = key;
    void previewVersion(requestedVersionId);
  }, [expanded, initialAction, initialVersionId, loading, mediaId, versions]);

  useEffect(() => {
    if (!selectedVersionId) return;
    const row = rowRefs.current[selectedVersionId];
    if (row && typeof row.scrollIntoView === "function") {
      row.scrollIntoView({ block: "nearest" });
    }
  }, [selectedVersionId]);

  const pageToVersion = (versionId: string | undefined) => {
    if (!versionId) return;
    setListOpen(true);
    void previewVersion(versionId);
  };
  const goToPreviousVersion = () => pageToVersion(selectedIndex > 0 ? versions[selectedIndex - 1].version_id : undefined);
  const goToNextVersion = () => pageToVersion(selectedIndex >= 0 && selectedIndex < versions.length - 1 ? versions[selectedIndex + 1].version_id : undefined);
  const pageRef = useRef({ goToPreviousVersion, goToNextVersion });
  pageRef.current = { goToPreviousVersion, goToNextVersion };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.key === "ArrowUp") {
        event.preventDefault();
        pageRef.current.goToPreviousVersion();
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        pageRef.current.goToNextVersion();
      }
    };
    // Capture phase so the shortcut still works while the Markdown editor holds focus.
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, []);

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
      setSelectedVersionId(saved.version_id);
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

  const closeEditor = () => {
    if (editorDirty && !window.confirm("当前修改尚未保存，确定收起校对吗？")) return;
    setEditor(null);
    setSelectedVersionId(null);
    setTimeline(null);
    setTimelineError(null);
    setSaveSuccess(null);
  };

  const enterBatchMode = () => {
    setBatchError(null);
    setBatchMode(true);
    setBatchResult(null);
    setListOpen(true);
  };

  const cancelBatch = () => {
    setBatchMode(false);
    setBatchSelectedIds([]);
    setBatchConfirmOpen(false);
    setBatchError(null);
  };

  const toggleBatchRow = (versionId: string, checked: boolean) => {
    setBatchSelectedIds((current) => checked
      ? (current.includes(versionId) ? current : [...current, versionId])
      : current.filter((id) => id !== versionId));
  };

  const selectAllDeletable = () => setBatchSelectedIds(deletableVersionIds);

  const openBatchConfirm = () => {
    if (batchSelectedIds.length === 0 || batchBusy) return;
    setBatchError(null);
    // Remember the labels the user saw when confirming, so the result list stays
    // readable after the deleted rows leave the list.
    setBatchConfirmLabels(Object.fromEntries(versions.map((version, index) => [version.version_id, `版本 ${index + 1}`])));
    setBatchRequestKey(newIdempotencyKey());
    setBatchConfirmOpen(true);
  };

  const closeBatchConfirm = () => {
    if (batchBusy) return;
    setBatchConfirmOpen(false);
    setBatchError(null);
  };

  const runBatchDelete = async () => {
    if (batchBusy || batchSelectedIds.length === 0) return;
    // One idempotency key per confirmation action: a retry of the same action reuses it.
    const requestIdempotencyKey = batchRequestKey ?? newIdempotencyKey();
    setBatchRequestKey(requestIdempotencyKey);
    setBatchBusy(true);
    setBatchError(null);
    try {
      const result = await adminMediaApi.bulkDeleteVersions(mediaId, batchSelectedIds, requestIdempotencyKey);
      setBatchResult(result);
      const removed = new Set(
        result.items
          .filter((item) => item.status === "deleted")
          .map((item) => item.version_id),
      );
      if (removed.size > 0) {
        setVersions((current) => current.filter((version) => !removed.has(version.version_id)));
      }
      if (editor && removed.has(editor.baseVersionId)) {
        setEditor(null);
        setSelectedVersionId(null);
        setTimeline(null);
        setTimelineError(null);
        setSaveSuccess(null);
      }
      // Partial success keeps the remaining selectable items for a retry.
      setBatchSelectedIds((current) => current.filter((id) => !removed.has(id)));
      setBatchConfirmOpen(false);
      if (removed.size > 0) {
        await loadVersions();
        await onChanged?.();
      }
    } catch (caught: any) {
      setBatchError(caught?.message || String(caught));
    } finally {
      setBatchBusy(false);
    }
  };

  const listColumn = (
    <>
      <div className="flex items-center justify-between gap-2 border-b border-border bg-surface-muted/30 px-3 py-2.5">
        <div className="min-w-0">
          <h4 className="text-ui-sm font-semibold text-foreground">转录版本</h4>
          <p className="mt-0.5 text-ui-xs text-muted-foreground">选择一个版本进入校对工作区</p>
        </div>
        <span className="shrink-0 text-ui-xs text-muted-foreground">{versions.length} 个版本</span>
      </div>
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <div className="flex items-center gap-1">
          <IconButton label="上一版" tooltip="选择上一个版本（Alt+↑）" disabled={!hasPreviousVersion || batchBusy} onClick={goToPreviousVersion}>
            <ArrowUp className="size-4" />
          </IconButton>
          <IconButton label="下一版" tooltip="选择下一个版本（Alt+↓）" disabled={!hasNextVersion || batchBusy} onClick={goToNextVersion}>
            <ArrowDown className="size-4" />
          </IconButton>
        </div>
        <span className="min-w-0 flex-1 truncate text-ui-xs text-muted-foreground" title="Alt+↑ / Alt+↓">
          <kbd className="rounded-ui-sm border border-border bg-surface-muted/60 px-1 font-sans">Alt+↑</kbd>
          {" / "}
          <kbd className="rounded-ui-sm border border-border bg-surface-muted/60 px-1 font-sans">Alt+↓</kbd>
          {" 快速翻页"}
        </span>
        {!batchMode ? (
          <Button size="sm" variant="outline" type="button" aria-pressed={false} onClick={enterBatchMode} disabled={versions.length === 0 || batchBusy}>
            批量选择
          </Button>
        ) : null}
      </div>
      {batchMode ? (
        <div className="flex flex-wrap items-center gap-2 border-b border-border bg-surface-muted/30 px-3 py-2">
          <Checkbox
            aria-label="全选可删除版本"
            checked={deletableVersionIds.length > 0 && deletableVersionIds.every((id) => batchSelectedSet.has(id))}
            disabled={deletableVersionIds.length === 0 || batchBusy}
            onChange={(event) => (event.target.checked ? selectAllDeletable() : setBatchSelectedIds([]))}
          />
          <span className="min-w-0 flex-1 text-ui-xs text-muted-foreground">全选可删除版本</span>
          <Button size="sm" variant="ghost" type="button" onClick={cancelBatch} disabled={batchBusy}>取消</Button>
        </div>
      ) : null}
      <div className="min-h-0 max-h-[18rem] flex-1 overflow-y-auto lg:max-h-none">
        {versions.map((version, index) => {
          const busy = busyVersionId === version.version_id;
          const isEditing = editor?.baseVersionId === version.version_id;
          const blockReason = blockReasons[version.version_id];
          const blockReasonId = `version-delete-block-${version.version_id}`;
          const batchChecked = batchSelectedSet.has(version.version_id);
          const itemResult = batchResultByVersionId.get(version.version_id);
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
            <article
              key={version.version_id}
              ref={(node) => { rowRefs.current[version.version_id] = node; }}
              className={`border-b border-border p-3 last:border-b-0 ${isEditing ? "bg-primary/5" : "bg-background"}`}
            >
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                {batchMode ? (
                  <span className="-m-2 inline-flex shrink-0 p-2">
                    <Checkbox
                      aria-label={`选择版本 ${index + 1}`}
                      aria-describedby={blockReason ? blockReasonId : undefined}
                      title={blockReason ?? "选择此版本"}
                      checked={batchChecked}
                      disabled={Boolean(blockReason) || batchBusy || itemResult?.status === "deleted"}
                      onChange={(event) => toggleBatchRow(version.version_id, event.target.checked)}
                    />
                  </span>
                ) : null}
                <button
                  type="button"
                  aria-pressed={isEditing}
                  className="flex min-w-0 flex-1 flex-wrap items-center gap-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => void previewVersion(version.version_id)}
                >
                  <span className="shrink-0 text-ui-sm font-semibold text-foreground">版本 {index + 1}</span>
                  <Badge variant={version.is_current ? "success" : "secondary"}>{version.is_current ? "当前正式版本" : statusLabel(version.publication_status)}</Badge>
                  <Badge variant="secondary">{statusLabel(version.review_status)}</Badge>
                  <span className="text-ui-xs text-muted-foreground">{sourceLabel(version)}</span>
                </button>
                <Button
                  size="sm"
                  variant={isEditing ? "secondary" : "outline"}
                  disabled={busy}
                  aria-expanded={isEditing}
                  onClick={() => isEditing ? closeEditor() : void previewVersion(version.version_id)}
                >
                  {isEditing ? "收起校对" : "校对内容"}
                </Button>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-ui-xs text-muted-foreground">
                <span>审核：{statusLabel(version.review_status)}</span>
                {version.review_note && <span className="max-w-full truncate" title={version.review_note}>· {version.review_note}</span>}
                {(version.scheme_name || version.scheme_deleted) && <div className="flex flex-wrap items-center gap-1.5" data-testid="version-scheme-line"><span>转录方案：{version.scheme_name || "原转录配置已删除"}</span>{version.scheme_name && version.scheme_deleted && <Badge variant="secondary">原转录配置已删除</Badge>}</div>}
                <div className="flex flex-wrap items-center gap-2 sm:ml-auto">
                  {version.review_status === "awaiting_review" && <>
                    <Button size="sm" className="h-8" disabled={busy} onClick={() => void reviewVersion(version.version_id, true)}>审核通过</Button>
                    <Button size="sm" variant="outline" className="h-8" disabled={busy} onClick={() => void reviewVersion(version.version_id, false)}>拒绝</Button>
                  </>}
                  <Button size="sm" className="h-8" disabled={busy || !canPublish} aria-describedby={!canPublish ? publishHintId : undefined} onClick={() => void publishVersion(version.version_id)}>{publishLabel}</Button>
                </div>
              </div>
              {version.review_status === "awaiting_review" && <div className="mt-2 max-w-xl">
                <label htmlFor={`review-note-${version.version_id}`} className="sr-only">审核备注 {version.version_id}</label>
                <Input id={`review-note-${version.version_id}`} aria-label={`审核备注 ${version.version_id}`} className="h-8 text-ui-xs" placeholder="审核备注（可选）" value={reviewNote[version.version_id] || ""} onChange={(event) => setReviewNote((current) => ({ ...current, [version.version_id]: event.target.value }))} />
              </div>}
              {publishHint && <p id={publishHintId} className="mt-1 text-ui-xs text-muted-foreground">{publishHint}</p>}
              {batchMode && blockReason && <p id={blockReasonId} className="mt-1 text-ui-xs text-muted-foreground">不能删除：{blockReason}</p>}
              {batchMode && batchResultByVersionId.has(version.version_id) && (
                <p className={`mt-1 text-ui-xs ${itemResult?.status === "deleted" ? "text-muted-foreground" : "text-destructive"}`}>
                  {itemResult?.status === "deleted" ? "已删除" : `跳过：${itemResult?.reason || "该版本当前不能删除"}`}
                </p>
              )}
            </article>
          );
        })}
      </div>
      {batchMode ? (
        <div className="flex flex-wrap items-center gap-2 border-t border-border bg-surface-muted/30 px-3 py-2">
          <span className="text-ui-xs font-medium text-foreground" role="status">已选 {batchSelectedIds.length} 个</span>
          <div className="flex flex-1 flex-wrap items-center justify-end gap-2">
            <Button size="sm" variant="destructive" type="button" className="h-8" disabled={batchSelectedIds.length === 0 || batchBusy} onClick={openBatchConfirm}>
              <Trash2 className="size-4" />
              {batchBusy ? "删除中…" : "删除所选"}
            </Button>
            <Button size="sm" variant="outline" type="button" className="h-8" disabled={batchBusy} onClick={cancelBatch}>取消</Button>
          </div>
        </div>
      ) : null}
      {batchResult && (
        <div className="space-y-2 border-t border-border px-3 py-2" data-testid="version-bulk-delete-result" role="status">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={batchResult.skipped_count > 0 ? "warning" : "success"}>成功 {batchResult.deleted_count} · 跳过 {batchResult.skipped_count}</Badge>
            <Button size="sm" variant="ghost" type="button" className="h-8" onClick={() => setBatchResult(null)}>知道了</Button>
          </div>
          {batchResult.skipped_count > 0 && (
            <ul className="space-y-1 text-ui-xs text-muted-foreground">
              {batchResult.items.filter((item) => item.status !== "deleted").map((item) => (
                <li key={item.version_id} className="break-words">
                  {batchConfirmLabels[item.version_id] ?? "不在当前列表中的版本"}：{item.reason || "该版本不能删除"}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {batchError && <p className="border-t border-border px-3 py-2 text-ui-xs text-destructive" role="alert">删除失败：{batchError}</p>}
    </>
  );

  const rightColumn: ReactNode = (
    <div className="min-w-0 space-y-3">
      {editor && selectedVersionId && (() => {
        const selectedIndex = versions.findIndex((version) => version.version_id === selectedVersionId);
        // The list may briefly lag behind the editor (a just-saved revision, a concurrent reload);
        // keep the校对工作区 mounted instead of tearing it down while the list catches up.
        const selectedVersion = selectedIndex >= 0 ? versions[selectedIndex] : versions[0];
        if (!selectedVersion) return null;
        const busy = busyVersionId === selectedVersion.version_id;
        return (
          <section className="min-w-0 rounded-ui-md border border-border bg-background p-4" aria-label="当前版本校对工作区">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
              <div className="min-w-0">
                <p className="text-ui-xs text-muted-foreground">当前校对版本</p>
                <h4 className="mt-1 flex flex-wrap items-center gap-2 text-ui-sm font-semibold">
                  版本 {Math.max(selectedIndex, 0) + 1}
                  <Badge variant="secondary">{sourceLabel(selectedVersion)}</Badge>
                </h4>
              </div>
              <Button size="sm" variant="outline" onClick={closeEditor}>收起校对</Button>
            </div>
            <details className="mt-3 text-ui-xs text-muted-foreground">
              <summary className="cursor-pointer select-none font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">查看技术详情</summary>
              <dl className="mt-2 grid gap-1 rounded-ui-sm bg-surface-muted/60 p-3 sm:grid-cols-2">
                <div><dt className="inline">版本编号：</dt> <dd className="inline break-all font-mono">{selectedVersion.version_id}</dd></div>
                <div><dt className="inline">Profile 标识：</dt> <dd className="inline break-all font-mono">{selectedVersion.profile_id || "—"}</dd></div>
                <div><dt className="inline">Provider 标识：</dt> <dd className="inline break-all font-mono">{selectedVersion.provider_key || "—"}</dd></div>
                <div><dt className="inline">模型：</dt> <dd className="inline break-all font-mono">{selectedVersion.model_id ? `${selectedVersion.model_id}@${selectedVersion.model_revision || "—"}` : "—"}</dd></div>
              </dl>
            </details>
            <section className="mt-4 space-y-2" aria-label="视频时间轴校对">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h5 className="text-ui-xs font-medium text-foreground">视频校对</h5>
                {timelineError && <span className="text-ui-xs text-destructive">{timelineError}</span>}
              </div>
              <SynchronizedVideoTranscript
                mediaId={mediaId}
                mediaUrl={`/api/admin/media/${encodeURIComponent(mediaId)}/preview`}
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
          </section>
        );
      })()}
    </div>
  );

  return (
    <div className={embedded ? "min-w-0 space-y-3" : "mt-3 min-w-0 border-t border-border pt-3"}>
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
        </div>
      )}
      {expanded && (
        <div className="grid min-w-0 grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(14rem,18rem)_minmax(0,1fr)]">
          <details
            open={listOpen}
            onToggle={(event) => setListOpen((event.currentTarget as HTMLDetailsElement).open)}
            className="min-w-0 overflow-hidden rounded-ui-md border border-border bg-background lg:contents"
            data-testid="version-list-region"
          >
            <summary
              className="flex cursor-pointer select-none items-center justify-between gap-3 border-b border-border bg-surface-muted/30 px-3 py-2.5 text-ui-sm font-semibold text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:hidden"
            >
              <span className="min-w-0 truncate">
                {batchMode
                  ? `批量选择 · 已选 ${batchSelectedIds.length} 个 · 共 ${versions.length} 个版本`
                  : `转录版本 · 共 ${versions.length} 个${selectedIndex >= 0 ? ` · 当前第 ${selectedIndex + 1} 个` : ""}`}
              </span>
              <span className="shrink-0 text-ui-xs text-muted-foreground">{listOpen ? "收起" : "展开"}</span>
            </summary>
            <section
              aria-label="转录版本列表"
              className="flex min-w-0 flex-col border-0 bg-background lg:sticky lg:top-0 lg:max-h-[calc(100vh-12rem)] lg:h-auto lg:w-full lg:overflow-hidden lg:rounded-ui-md lg:border lg:border-border"
            >
              {listColumn}
            </section>
          </details>
          {rightColumn}
        </div>
      )}
      <Dialog open={batchConfirmOpen} onOpenChange={(open) => { if (!open) closeBatchConfirm(); }}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>删除所选的 {batchSelectedVersions.length} 个转录版本</DialogTitle>
            <DialogDescription>
              删除后无法恢复：这些版本的转写正文、时间轴及系统保存的转写产物文件都会被永久删除，已发布的索引内容不受影响。
            </DialogDescription>
          </DialogHeader>
          <ul className="max-h-64 space-y-2 overflow-y-auto text-ui-sm">
            {batchSelectedVersions.map((version) => (
              <li key={version.version_id} className="rounded-ui-sm border border-border px-3 py-2">
                <p className="font-medium text-foreground">
                  版本 {versions.findIndex((item) => item.version_id === version.version_id) + 1} · {sourceLabel(version)}
                </p>
                <p className="mt-0.5 text-ui-xs text-muted-foreground">创建时间：{formatVersionTime(version.created_at)}</p>
              </li>
            ))}
          </ul>
          {batchError && <p className="rounded-ui-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-ui-sm text-destructive" role="alert">{batchError}</p>}
          <p className="text-ui-xs text-muted-foreground">本次操作不可撤销，确认前请核对上述版本。</p>
          <DialogFooter>
            <Button variant="outline" disabled={batchBusy} onClick={closeBatchConfirm}>取消</Button>
            <Button variant="destructive" disabled={batchBusy || batchSelectedVersions.length === 0} onClick={() => void runBatchDelete()}>
              {batchBusy ? "正在删除…" : `确认删除 ${batchSelectedVersions.length} 个版本`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
