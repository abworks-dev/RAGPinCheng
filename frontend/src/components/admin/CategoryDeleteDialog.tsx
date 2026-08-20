import { useCallback, useEffect, useState } from "react";
import { Trash2, TriangleAlert } from "lucide-react";
import { adminContentApi } from "../../api/admin/content";
import type { CategoryDeletePreview, CategoryDeleteResult, ManagedCategory } from "../../types";
import { Alert, AlertDescription, AlertTitle } from "../ui/alert";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { ErrorState } from "../ui/error-state";
import { Input } from "../ui/input";
import { LoadingState } from "../ui/loading-state";

export function CategoryDeleteDialog({
  category,
  canForceDelete = false,
  onClose,
  onDeleted,
}: {
  category: ManagedCategory | null;
  canForceDelete?: boolean;
  onClose: () => void;
  onDeleted: (result: CategoryDeleteResult) => void | Promise<void>;
}) {
  const [preview, setPreview] = useState<CategoryDeletePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [forceMode, setForceMode] = useState(false);
  const [typedPath, setTypedPath] = useState("");
  const [forceAcknowledged, setForceAcknowledged] = useState(false);

  const loadPreview = useCallback(async () => {
    if (!category) return;
    setLoading(true);
    setError(null);
    setPreview(null);
    try {
      setPreview(await adminContentApi.categoryDeletePreview(category.id));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "删除检查失败");
    } finally {
      setLoading(false);
    }
  }, [category]);

  useEffect(() => {
    if (category) {
      setForceMode(false);
      setTypedPath("");
      setForceAcknowledged(false);
      void loadPreview();
    }
  }, [category, loadPreview]);

  const remove = async () => {
    if (!category || !preview?.can_delete) return;
    setDeleting(true);
    setError(null);
    try {
      const result = await adminContentApi.deleteCategory(category.id, preview.version);
      await onDeleted(result);
      onClose();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "删除文件夹失败");
      await loadPreview();
    } finally {
      setDeleting(false);
    }
  };

  const forceRemove = async () => {
    if (!category || !preview?.can_force_delete || typedPath !== preview.full_path || !forceAcknowledged) return;
    setDeleting(true);
    setError(null);
    try {
      const result = await adminContentApi.deleteCategory(category.id, preview.version, {
        force: true,
        typedPath,
      });
      await onDeleted(result);
      onClose();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "强制永久删除文件夹失败");
      await loadPreview();
    } finally {
      setDeleting(false);
    }
  };

  const blockerCount = preview
    ? preview.content_count + preview.pending_request_count + preview.active_upload_count + preview.active_reclassification_count
    : 0;

  return <Dialog open={Boolean(category)} onOpenChange={(open) => { if (!open && !deleting) onClose(); }}>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{forceMode ? "强制永久删除文件夹" : "删除文件夹"}</DialogTitle>
        <DialogDescription>{forceMode
          ? "永久删除关联资料、上传任务、索引数据和无引用文件。审计记录仍会保留。"
          : "删除后文件夹及其空子文件夹将不再显示，历史审计记录仍会保留。"}</DialogDescription>
      </DialogHeader>
      {loading ? <LoadingState className="min-h-40 border-0" label="正在检查文件夹…" /> : error && !preview ? (
        <ErrorState className="py-6" title="删除检查失败" description={error} action={<Button size="sm" variant="outline" onClick={() => void loadPreview()}>重新检查</Button>} />
      ) : preview ? <div className="space-y-4">
        <div className="rounded-ui-md border border-border bg-surface-muted/40 px-3 py-3 text-ui-sm">
          <p className="text-ui-xs text-muted-foreground">将删除</p>
          <p className="mt-1 break-words font-medium">{preview.full_path}</p>
        </div>
        <dl className="grid grid-cols-[minmax(0,1fr)_max-content] gap-x-4 gap-y-2 text-ui-sm">
          <dt className="text-muted-foreground">子文件夹</dt><dd className="tabular-nums">{preview.descendant_count} 个</dd>
          <dt className="text-muted-foreground">合计删除</dt><dd className="tabular-nums">{preview.folder_count} 个文件夹</dd>
          <dt className="text-muted-foreground">同级编号调整</dt><dd className="tabular-nums">{preview.renumbered_sibling_count} 个</dd>
        </dl>
        {!preview.can_delete && !forceMode && <Alert variant="destructive" role="alert">
          <TriangleAlert className="size-4" aria-hidden="true" />
          <AlertTitle>当前不能删除</AlertTitle>
          <AlertDescription className="space-y-1">
            <p>请先处理文件夹及其子文件夹中的资料和待处理任务。</p>
            {preview.content_count > 0 && <p>资料（含回收站）：{preview.content_count} 份</p>}
            {preview.pending_request_count > 0 && <p>待处理文件夹申请：{preview.pending_request_count} 个</p>}
            {preview.active_upload_count > 0 && <p>进行中的上传任务：{preview.active_upload_count} 个</p>}
            {preview.active_reclassification_count > 0 && <p>进行中的分类调整：{preview.active_reclassification_count} 个</p>}
            {preview.active_index_count > 0 && <p>进行中的索引任务：{preview.active_index_count} 个</p>}
            {preview.protected_category && <p>系统默认一级分类受保护，不能强制永久删除。</p>}
            {preview.media_transcript_count > 0 && <p>包含 {preview.media_transcript_count} 份视频转录稿，请先在转录任务中处理。</p>}
          </AlertDescription>
        </Alert>}
        {preview.can_delete && <Alert variant="warning" role="status"><AlertTitle>此操作会删除整棵空目录</AlertTitle><AlertDescription>确认后无法在页面恢复；剩余同级文件夹将自动使用连续编号。</AlertDescription></Alert>}
        {forceMode && <div className="space-y-4">
          <Alert variant="destructive" role="alert">
            <TriangleAlert className="size-4" aria-hidden="true" />
            <AlertTitle>此操作不可恢复</AlertTitle>
            <AlertDescription>系统会终止关联任务，并永久删除资料文件、发布副本、向量索引和父文档记录。</AlertDescription>
          </Alert>
          <dl className="grid grid-cols-[minmax(0,1fr)_max-content] gap-x-4 gap-y-2 text-ui-sm">
            <dt className="text-muted-foreground">资料</dt><dd className="tabular-nums">{preview.content_count} 份</dd>
            <dt className="text-muted-foreground">其中回收站资料</dt><dd className="tabular-nums">{preview.archived_content_count} 份</dd>
            <dt className="text-muted-foreground">上传任务</dt><dd className="tabular-nums">{preview.upload_batch_count} 个</dd>
            <dt className="text-muted-foreground">进行中的索引任务</dt><dd className="tabular-nums">{preview.active_index_count} 个</dd>
          </dl>
          {preview.protected_category && <Alert variant="destructive"><AlertTitle>系统分类受保护</AlertTitle><AlertDescription>系统默认一级分类不能强制永久删除。</AlertDescription></Alert>}
          {preview.media_transcript_count > 0 && <Alert variant="destructive"><AlertTitle>包含视频转录稿</AlertTitle><AlertDescription>请先在转录任务中处理 {preview.media_transcript_count} 份视频转录稿。</AlertDescription></Alert>}
          <label className="block space-y-2 text-ui-sm">
            <span className="font-medium">输入完整目录路径确认</span>
            <Input value={typedPath} onChange={(event) => setTypedPath(event.target.value)} placeholder={preview.full_path} disabled={deleting} />
          </label>
          <label className="flex items-start gap-2 text-ui-sm">
            <Checkbox className="mt-0.5" checked={forceAcknowledged} onChange={() => setForceAcknowledged((current) => !current)} disabled={deleting} />
            <span>我确认永久删除回收站、上传任务、索引任务和关联文件。</span>
          </label>
        </div>}
        {error && <p className="text-ui-sm text-destructive" role="alert">{error}</p>}
      </div> : null}
      <DialogFooter>
        <Button variant="outline" onClick={() => forceMode ? setForceMode(false) : onClose()} disabled={deleting}>{forceMode ? "返回" : "取消"}</Button>
        {!forceMode && !preview?.can_delete && canForceDelete && <Button variant="destructive" onClick={() => setForceMode(true)} disabled={!preview?.can_force_delete || deleting || loading}>
          <TriangleAlert className="size-4" />强制永久删除
        </Button>}
        {!forceMode && <Button variant="destructive" onClick={() => void remove()} disabled={!preview?.can_delete || blockerCount > 0 || deleting || loading}>
          <Trash2 className="size-4" />{deleting ? "删除中…" : "确认删除文件夹"}
        </Button>}
        {forceMode && <Button variant="destructive" onClick={() => void forceRemove()} disabled={!preview?.can_force_delete || typedPath !== preview.full_path || !forceAcknowledged || deleting || loading}>
          <Trash2 className="size-4" />{deleting ? "永久删除中…" : "确认永久删除"}
        </Button>}
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
