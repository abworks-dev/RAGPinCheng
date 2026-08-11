import { useCallback, useEffect, useRef, useState } from "react";
import { Check, FileText, RefreshCw, Rocket, Send, Upload, X } from "lucide-react";
import { api } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { LoadingState } from "../../components/ui/loading-state";
import { Select } from "../../components/ui/select";
import { toast } from "../../components/ui/toast";
import { useAuth } from "../../context/AuthContext";
import type { ContentPermission, ManagedCategory, ManagedContentItem, ManagedUploadResponse } from "../../types";

const statusLabel: Record<string, string> = {
  draft: "待提交",
  awaiting_review: "待确认",
  approved: "已确认",
  rejected: "已退回",
  publishing: "发布中",
  published: "已发布",
  publication_failed: "发布失败",
  superseded: "历史版本",
};

const sourceLabel: Record<string, string> = {
  web: "网页上传",
  server: "后台导入",
  legacy: "历史迁移",
  transcription: "视频转写",
};

function statusVariant(status: string) {
  if (status === "published") return "success" as const;
  if (status.includes("failed") || status === "rejected") return "destructive" as const;
  if (status === "awaiting_review" || status === "publishing") return "warning" as const;
  return "secondary" as const;
}

export function AdminManagedContentPage() {
  const { state } = useAuth();
  const permissions = state.status === "authed" ? state.user.content_permissions || [] : [];
  const can = (permission: ContentPermission) => state.status === "authed" && (state.user.role === "admin" || permissions.includes(permission));
  const [items, setItems] = useState<ManagedContentItem[]>([]);
  const [categories, setCategories] = useState<ManagedCategory[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [categoryId, setCategoryId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [uploadResults, setUploadResults] = useState<ManagedUploadResponse["entries"]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async (refresh = false) => {
    refresh ? setRefreshing(true) : setLoading(true);
    setError(null);
    try {
      const [capabilities, categoryRows, itemRows] = await Promise.all([
        api.managedContentCapabilities(), api.managedCategories(), api.managedContentItems(),
      ]);
      setEnabled(capabilities.enabled);
      setCategories(categoryRows);
      setItems(itemRows);
      setCategoryId((current) => current || categoryRows[0]?.id || "");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "资料加载失败");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const upload = async () => {
    setUploading(true);
    setUploadResults([]);
    try {
      const result = await api.uploadManagedContent(files, categoryId);
      setUploadResults(result.entries);
      const accepted = result.entries.filter((entry) => entry.status === "accepted").length;
      const skipped = result.entries.length - accepted;
      toast.success(skipped ? `已接收 ${accepted} 个文件，跳过 ${skipped} 个` : `已接收 ${accepted} 个文件`);
      setFiles([]);
      if (fileInputRef.current) fileInputRef.current.value = "";
      await load(true);
    } catch (uploadError) {
      toast.error(uploadError instanceof Error ? uploadError.message : "上传失败");
    } finally {
      setUploading(false);
    }
  };

  const act = async (item: ManagedContentItem, action: string, operation: () => Promise<unknown>, success: string) => {
    const actionKey = `${item.version_id}:${action}`;
    setBusyAction(actionKey);
    try {
      await operation();
      toast.success(success);
      await load(true);
    } catch (actionError) {
      toast.error(actionError instanceof Error ? actionError.message : "操作失败");
    } finally {
      setBusyAction(null);
    }
  };

  const renderActions = (item: ManagedContentItem) => {
    const disabled = Boolean(busyAction) || refreshing || !enabled;
    return <div className="flex min-h-control-sm flex-wrap gap-2 sm:justify-end">
      {can("organize") && ["draft", "rejected"].includes(item.lifecycle_status) && (
        <Button size="sm" variant="outline" disabled={disabled} onClick={() => void act(item, "submit", () => api.submitManagedContent(item.version_id), "已提交确认")}>
          <Send className="size-4" />{busyAction === `${item.version_id}:submit` ? "提交中…" : "提交"}
        </Button>
      )}
      {can("review") && item.lifecycle_status === "awaiting_review" && <>
        <Button size="sm" disabled={disabled} onClick={() => void act(item, "approve", () => api.reviewManagedContent(item.version_id, true), "资料已确认")}>
          <Check className="size-4" />{busyAction === `${item.version_id}:approve` ? "确认中…" : "确认"}
        </Button>
        <Button size="sm" variant="outline" disabled={disabled} onClick={() => void act(item, "reject", () => api.reviewManagedContent(item.version_id, false), "资料已退回")}>
          <X className="size-4" />{busyAction === `${item.version_id}:reject` ? "退回中…" : "退回"}
        </Button>
      </>}
      {can("publish") && ["approved", "publication_failed"].includes(item.lifecycle_status) && (
        <Button size="sm" disabled={disabled} onClick={() => void act(item, "publish", () => api.publishManagedContent(item.version_id), "已进入发布队列")}>
          <Rocket className="size-4" />{busyAction === `${item.version_id}:publish` ? "发布中…" : "发布"}
        </Button>
      )}
    </div>;
  };

  return <section className="space-y-5" aria-labelledby="managed-content-title">
    <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <p className="text-ui-xs text-muted-foreground">资料管理</p>
        <h1 id="managed-content-title" className="mt-1 text-ui-2xl font-semibold text-foreground">资料工作流</h1>
        <p className="mt-1 text-ui-sm text-muted-foreground">上传资料，并按整理、确认、发布顺序完成入库。</p>
      </div>
      <Button size="sm" variant="outline" className="w-full sm:w-auto" onClick={() => void load(true)} disabled={loading || refreshing}>
        <RefreshCw className={refreshing ? "size-4 animate-spin" : "size-4"} />{refreshing ? "刷新中…" : "刷新"}
      </Button>
    </header>

    {!enabled && !loading && <div className="border border-warning/40 bg-warning/10 px-4 py-3 text-ui-sm" role="status">受管资料库当前未启用，上传和流程操作暂不可用。</div>}
    {error && <ErrorState title="资料列表加载失败" description={error} action={<Button size="sm" variant="outline" onClick={() => void load()}>重新加载</Button>} />}

    {can("organize") && <section className="space-y-4 border-y border-border py-5" aria-labelledby="managed-upload-title">
      <div>
        <h2 id="managed-upload-title" className="text-ui-base font-semibold">上传资料</h2>
        <p className="mt-1 text-ui-xs text-muted-foreground">先选择分类，再选择文件，最后提交上传。</p>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(14rem,18rem)_minmax(0,1fr)_auto] lg:items-end">
        <label className="space-y-1.5 text-ui-sm font-medium">
          <span>1. 资料分类</span>
          <Select value={categoryId} onChange={(event) => setCategoryId(event.target.value)} disabled={loading || uploading || categories.length === 0}>
            {categories.length === 0 && <option value="">暂无可用分类</option>}
            {categories.map((category) => <option key={category.id} value={category.id}>{category.display_code} {category.display_name}</option>)}
          </Select>
        </label>
        <div className="space-y-1.5">
          <span className="block text-ui-sm font-medium">2. 资料文件</span>
          <label className="flex min-h-20 cursor-pointer items-center gap-3 rounded-ui-md border border-dashed border-input bg-background px-4 py-3 transition-colors hover:bg-surface-muted focus-within:ring-2 focus-within:ring-ring">
            <FileText className="size-5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1">
              <span className="block text-ui-sm font-medium">{files.length ? `已选择 ${files.length} 个文件` : "选择一个或多个文件"}</span>
              <span className="mt-0.5 block break-words text-ui-xs text-muted-foreground">PDF、Markdown、Word、Excel 或 PPT</span>
            </span>
            <span className="shrink-0 text-ui-sm font-medium text-primary">浏览</span>
            <input ref={fileInputRef} aria-label="选择资料文件" type="file" multiple accept=".pdf,.md,.docx,.xlsx,.pptx" className="sr-only" disabled={uploading} onChange={(event) => { setFiles(Array.from(event.target.files || [])); setUploadResults([]); }} />
          </label>
          {files.length > 0 && <ul className="space-y-1 text-ui-xs text-muted-foreground" aria-label="已选择的文件">
            {files.map((file) => <li key={`${file.name}-${file.lastModified}`} className="break-all">{file.name}</li>)}
          </ul>}
        </div>
        <Button className="w-full lg:w-auto" onClick={() => void upload()} disabled={!enabled || uploading || !categoryId || files.length === 0}>
          <Upload className="size-4" />{uploading ? "上传中…" : "3. 上传"}
        </Button>
      </div>
      {uploadResults.length > 0 && <div className="space-y-2" aria-live="polite">
        <p className="text-ui-sm font-medium">本次上传结果</p>
        <ul className="divide-y divide-border border-y border-border text-ui-sm">
          {uploadResults.map((entry) => <li key={entry.filename} className="flex flex-col gap-1 py-2 sm:flex-row sm:items-center sm:justify-between">
            <span className="min-w-0 break-all">{entry.filename}</span>
            <span className="flex shrink-0 items-center gap-2"><Badge variant={entry.status === "accepted" ? "success" : "warning"}>{entry.status === "accepted" ? "已接收" : "已跳过"}</Badge>{entry.reason && <span className="text-ui-xs text-muted-foreground">{entry.reason}</span>}</span>
          </li>)}
        </ul>
      </div>}
    </section>}

    <section className="space-y-3" aria-labelledby="managed-list-title">
      <div className="flex items-center justify-between gap-3">
        <h2 id="managed-list-title" className="text-ui-base font-semibold">资料列表</h2>
        {!loading && !error && <span className="text-ui-xs text-muted-foreground" aria-live="polite">共 {items.length} 份资料</span>}
      </div>
      {loading ? <LoadingState className="min-h-48 border border-border" label="正在加载资料…" /> : !error && items.length === 0 ? (
        <EmptyState title="暂无资料" description="上传第一份资料后，流程状态会显示在这里。" />
      ) : !error && <>
        <div className="hidden overflow-x-auto border border-border md:block">
          <table className="w-full min-w-[48rem] text-ui-sm">
            <thead className="border-b border-border bg-surface-muted text-left text-muted-foreground"><tr><th className="px-4 py-3 font-medium">资料</th><th className="px-4 py-3 font-medium">分类</th><th className="px-4 py-3 font-medium">状态</th><th className="px-4 py-3 font-medium">来源</th><th className="px-4 py-3 text-right font-medium">操作</th></tr></thead>
            <tbody className="divide-y divide-border">{items.map((item) => <tr key={item.item_id}><td className="max-w-xs px-4 py-3"><p className="break-words font-medium">{item.title}</p><p className="mt-0.5 break-all text-ui-xs text-muted-foreground">{item.original_filename} · v{item.version_number}</p></td><td className="px-4 py-3">{item.category_label}</td><td className="px-4 py-3"><Badge variant={statusVariant(item.lifecycle_status)}>{statusLabel[item.lifecycle_status] || "未知状态"}</Badge></td><td className="px-4 py-3">{sourceLabel[item.source_origin] || "其他来源"}</td><td className="px-4 py-3">{renderActions(item)}</td></tr>)}</tbody>
          </table>
        </div>
        <ul className="divide-y divide-border border-y border-border md:hidden">
          {items.map((item) => <li key={item.item_id} className="space-y-3 py-4">
            <div className="flex items-start justify-between gap-3"><div className="min-w-0"><p className="break-words font-medium">{item.title}</p><p className="mt-1 break-all text-ui-xs text-muted-foreground">{item.original_filename} · v{item.version_number}</p></div><Badge className="shrink-0" variant={statusVariant(item.lifecycle_status)}>{statusLabel[item.lifecycle_status] || "未知状态"}</Badge></div>
            <dl className="grid grid-cols-[4rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-ui-sm"><dt className="text-muted-foreground">分类</dt><dd className="break-words">{item.category_label}</dd><dt className="text-muted-foreground">来源</dt><dd>{sourceLabel[item.source_origin] || "其他来源"}</dd></dl>
            {renderActions(item)}
          </li>)}
        </ul>
      </>}
    </section>
  </section>;
}
