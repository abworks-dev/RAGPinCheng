import { useEffect, useState } from "react";
import { Check, RefreshCw, Rocket, Send, Upload, X } from "lucide-react";
import { api } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import type { ContentPermission, ManagedCategory, ManagedContentItem } from "../../types";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { toast } from "../../components/ui/toast";

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

export function AdminManagedContentPage() {
  const { state } = useAuth();
  const permissions = state.status === "authed" ? state.user.content_permissions || [] : [];
  const can = (permission: ContentPermission) => state.status === "authed" && (state.user.role === "admin" || permissions.includes(permission));
  const [items, setItems] = useState<ManagedContentItem[]>([]);
  const [categories, setCategories] = useState<ManagedCategory[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [categoryId, setCategoryId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const [capabilities, categoryRows, itemRows] = await Promise.all([
        api.managedContentCapabilities(), api.managedCategories(), api.managedContentItems(),
      ]);
      setEnabled(capabilities.enabled);
      setCategories(categoryRows);
      setItems(itemRows);
      if (!categoryId && categoryRows.length) setCategoryId(categoryRows[0].id);
    } catch (error) { toast.error(error instanceof Error ? error.message : "资料加载失败"); }
  };

  useEffect(() => { void load(); }, []);

  const upload = async () => {
    setBusy(true);
    try {
      const result = await api.uploadManagedContent(files, categoryId);
      const accepted = result.entries.filter((entry) => entry.status === "accepted").length;
      toast.success(`已接收 ${accepted} 个文件`);
      setFiles([]);
      await load();
    } catch (error) { toast.error(error instanceof Error ? error.message : "上传失败"); }
    finally { setBusy(false); }
  };

  const act = async (operation: () => Promise<unknown>, success: string) => {
    setBusy(true);
    try { await operation(); toast.success(success); await load(); }
    catch (error) { toast.error(error instanceof Error ? error.message : "操作失败"); }
    finally { setBusy(false); }
  };

  return <section className="space-y-6" aria-labelledby="managed-content-title">
    <div className="flex items-end justify-between gap-4"><div><p className="text-ui-xs text-muted-foreground">资料管理</p><h1 id="managed-content-title" className="mt-1 text-ui-2xl font-semibold">资料工作流</h1></div><Button size="sm" variant="outline" onClick={() => void load()}><RefreshCw className="size-4" />刷新</Button></div>
    {!enabled && <div className="border border-warning/40 bg-warning/10 px-4 py-3 text-ui-sm">受管资料库当前未启用</div>}
    {can("organize") && <section className="border-y border-border py-5"><div className="grid gap-3 md:grid-cols-[minmax(12rem,18rem)_1fr_auto]"><select aria-label="上传分类" className="h-control-md rounded-ui-md border border-input bg-background px-3 text-ui-sm" value={categoryId} onChange={(event) => setCategoryId(event.target.value)}>{categories.map((category) => <option key={category.id} value={category.id}>{category.display_code} {category.display_name}</option>)}</select><input aria-label="选择资料文件" type="file" multiple accept=".pdf,.md,.docx,.xlsx,.pptx" className="h-control-md border border-input bg-background px-3 py-2 text-ui-sm" onChange={(event) => setFiles(Array.from(event.target.files || []))} /><Button onClick={() => void upload()} disabled={!enabled || busy || !categoryId || files.length === 0}><Upload className="size-4" />上传</Button></div></section>}
    <div className="overflow-x-auto border border-border"><table className="min-w-[48rem] w-full text-ui-sm"><thead className="bg-surface-muted text-left text-muted-foreground"><tr><th className="px-4 py-3">资料</th><th className="px-4 py-3">分类</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">来源</th><th className="px-4 py-3 text-right">操作</th></tr></thead><tbody className="divide-y divide-border">{items.map((item) => <tr key={item.item_id}><td className="px-4 py-3"><p className="font-medium">{item.title}</p><p className="text-ui-xs text-muted-foreground">{item.original_filename} · v{item.version_number}</p></td><td className="px-4 py-3">{item.category_label}</td><td className="px-4 py-3"><Badge variant={item.lifecycle_status === "published" ? "success" : item.lifecycle_status.includes("failed") || item.lifecycle_status === "rejected" ? "destructive" : "secondary"}>{statusLabel[item.lifecycle_status] || item.lifecycle_status}</Badge></td><td className="px-4 py-3">{item.source_origin}</td><td className="px-4 py-3"><div className="flex justify-end gap-2">{can("organize") && ["draft", "rejected"].includes(item.lifecycle_status) && <Button size="sm" variant="outline" disabled={busy || !enabled} onClick={() => void act(() => api.submitManagedContent(item.version_id), "已提交确认")}><Send className="size-4" />提交</Button>}{can("review") && item.lifecycle_status === "awaiting_review" && <><Button size="sm" disabled={busy || !enabled} onClick={() => void act(() => api.reviewManagedContent(item.version_id, true), "资料已确认")}><Check className="size-4" />确认</Button><Button size="sm" variant="outline" disabled={busy || !enabled} onClick={() => void act(() => api.reviewManagedContent(item.version_id, false), "资料已退回")}><X className="size-4" />退回</Button></>}{can("publish") && ["approved", "publication_failed"].includes(item.lifecycle_status) && <Button size="sm" disabled={busy || !enabled} onClick={() => void act(() => api.publishManagedContent(item.version_id), "已进入发布队列")}><Rocket className="size-4" />发布</Button>}</div></td></tr>)}</tbody></table>{items.length === 0 && <p className="p-8 text-center text-muted-foreground">暂无资料</p>}</div>
  </section>;
}
