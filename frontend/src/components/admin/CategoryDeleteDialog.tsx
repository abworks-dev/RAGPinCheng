import { useCallback, useEffect, useState } from "react";
import { Trash2, TriangleAlert } from "lucide-react";
import { adminContentApi } from "../../api/admin/content";
import type { CategoryDeletePreview, CategoryDeleteResult, ManagedCategory } from "../../types";
import { Alert, AlertDescription, AlertTitle } from "../ui/alert";
import { Button } from "../ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { ErrorState } from "../ui/error-state";
import { LoadingState } from "../ui/loading-state";

export function CategoryDeleteDialog({
  category,
  onClose,
  onDeleted,
}: {
  category: ManagedCategory | null;
  onClose: () => void;
  onDeleted: (result: CategoryDeleteResult) => void | Promise<void>;
}) {
  const [preview, setPreview] = useState<CategoryDeletePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [deleting, setDeleting] = useState(false);

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

  useEffect(() => { if (category) void loadPreview(); }, [category, loadPreview]);

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

  const blockerCount = preview
    ? preview.content_count + preview.pending_request_count + preview.active_upload_count + preview.active_reclassification_count
    : 0;

  return <Dialog open={Boolean(category)} onOpenChange={(open) => { if (!open && !deleting) onClose(); }}>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>删除文件夹</DialogTitle>
        <DialogDescription>删除后文件夹及其空子文件夹将不再显示，历史审计记录仍会保留。</DialogDescription>
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
        {!preview.can_delete && <Alert variant="destructive" role="alert">
          <TriangleAlert className="size-4" aria-hidden="true" />
          <AlertTitle>当前不能删除</AlertTitle>
          <AlertDescription className="space-y-1">
            <p>请先处理文件夹及其子文件夹中的资料和待处理任务。</p>
            {preview.content_count > 0 && <p>资料（含回收站）：{preview.content_count} 份</p>}
            {preview.pending_request_count > 0 && <p>待处理文件夹申请：{preview.pending_request_count} 个</p>}
            {preview.active_upload_count > 0 && <p>进行中的上传任务：{preview.active_upload_count} 个</p>}
            {preview.active_reclassification_count > 0 && <p>进行中的分类调整：{preview.active_reclassification_count} 个</p>}
          </AlertDescription>
        </Alert>}
        {preview.can_delete && <Alert variant="warning" role="status"><AlertTitle>此操作会删除整棵空目录</AlertTitle><AlertDescription>确认后无法在页面恢复；剩余同级文件夹将自动使用连续编号。</AlertDescription></Alert>}
        {error && <p className="text-ui-sm text-destructive" role="alert">{error}</p>}
      </div> : null}
      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={deleting}>取消</Button>
        <Button variant="destructive" onClick={() => void remove()} disabled={!preview?.can_delete || blockerCount > 0 || deleting || loading}>
          <Trash2 className="size-4" />{deleting ? "删除中…" : "确认删除文件夹"}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
