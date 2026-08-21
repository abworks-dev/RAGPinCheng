import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronLeft, Folder, FolderSync, Network, Play, RefreshCw, Settings2 } from "lucide-react";
import { adminMediaApi } from "../../api/admin/media";
import type { ExternalMediaEntry, ExternalMediaRoot, ExternalMediaSource, ManagedCategory, TranscriptionSchemeOption } from "../../types";
import { formatAdminDate, formatBytes } from "../../lib/admin-formatters";
import { Alert, AlertDescription, AlertTitle } from "../ui/alert";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card } from "../ui/card";
import { Checkbox } from "../ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { EmptyState } from "../ui/empty-state";
import { ErrorState } from "../ui/error-state";
import { Input } from "../ui/input";
import { LoadingState } from "../ui/loading-state";
import { Select } from "../ui/select";
import { CategoryTreePicker } from "./CategoryTreePicker";

type Props = {
  categories: ManagedCategory[];
  schemes: TranscriptionSchemeOption[];
  onOpenWorkbench: (mediaId: string) => void;
  onMediaChanged: () => Promise<void> | void;
};

const sourceStatus = {
  never_scanned: ["尚未扫描", "secondary"],
  scanning: ["扫描中", "warning"],
  available: ["可访问", "success"],
  unavailable: ["不可访问", "destructive"],
  scan_failed: ["扫描失败", "destructive"],
} as const;

const workflowLabel: Record<string, string> = {
  pending: "等待转录",
  running: "正在转录",
  succeeded: "转录完成",
  failed: "转录失败",
  cancelled: "已取消",
  awaiting_review: "待人工审核",
  review_approved: "审核通过",
  review_rejected: "审核驳回",
  publishing: "发布中",
  published: "已发布",
  publication_failed: "发布失败",
  parsing: "解析中",
  chunking: "分块中",
  embedding: "向量化中",
  done: "索引成功",
};

export function ExternalMediaSourcesPanel({ categories, schemes, onOpenWorkbench, onMediaChanged }: Props) {
  const [roots, setRoots] = useState<ExternalMediaRoot[]>([]);
  const [sources, setSources] = useState<ExternalMediaSource[]>([]);
  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [parent, setParent] = useState("");
  const [entries, setEntries] = useState<ExternalMediaEntry[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [entriesLoading, setEntriesLoading] = useState(false);
  const [busy, setBusy] = useState<"scan" | "enqueue" | "save" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [form, setForm] = useState({ name: "", rootAlias: "", relativePath: "", categoryId: "cat-05", schemeId: "", autoEnqueue: false, interval: "900" });

  const selectedSource = sources.find((source) => source.id === selectedSourceId) ?? null;
  const videos = entries.filter((entry) => entry.kind === "video");
  const selectable = videos.filter((entry) => entry.availability === "available" && !entry.transcription_job_id);
  const selectedSet = useMemo(() => new Set(selected), [selected]);

  const loadSources = useCallback(async () => {
    setLoading(true);
    try {
      const [rootItems, sourceItems] = await Promise.all([adminMediaApi.externalRoots(), adminMediaApi.externalSources()]);
      setRoots(rootItems);
      setSources(sourceItems);
      setSelectedSourceId((current) => current && sourceItems.some((item) => item.id === current) ? current : sourceItems[0]?.id || "");
      setError(null);
    } catch (cause) {
      if (cause instanceof TypeError && /is not a function/.test(cause.message)) {
        setRoots([]); setSources([]); setError(null);
      } else {
        setError(cause instanceof Error ? cause.message : "共享资料源加载失败");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const loadEntries = useCallback(async (sourceId: string, targetParent: string) => {
    if (!sourceId) { setEntries([]); return; }
    setEntriesLoading(true);
    try {
      const result = await adminMediaApi.externalEntries(sourceId, targetParent);
      setEntries(result.entries);
      setSelected([]);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "共享目录内容加载失败");
    } finally {
      setEntriesLoading(false);
    }
  }, []);

  useEffect(() => { void loadSources(); }, [loadSources]);
  useEffect(() => { setParent(""); }, [selectedSourceId]);
  useEffect(() => { void loadEntries(selectedSourceId, parent); }, [loadEntries, parent, selectedSourceId]);
  useEffect(() => {
    const firstScheme = schemes.find((item) => item.enabled && !item.archived && item.availability === "available");
    if (firstScheme) setForm((current) => ({ ...current, schemeId: current.schemeId || firstScheme.scheme_id }));
  }, [schemes]);

  async function createSource() {
    setBusy("save"); setNotice(null); setError(null);
    try {
      const created = await adminMediaApi.createExternalSource({
        name: form.name.trim(), root_alias: form.rootAlias, relative_path: form.relativePath.trim(),
        target_category_id: form.categoryId, default_scheme_id: form.schemeId,
        auto_enqueue: form.autoEnqueue, scan_interval_seconds: Number(form.interval),
      });
      setDialogOpen(false);
      await loadSources();
      setSelectedSourceId(created.id);
      setNotice("共享资料源已登记。请先扫描并核对文件数量，再批量加入转录队列。");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "共享资料源登记失败"); }
    finally { setBusy(null); }
  }

  async function scan() {
    if (!selectedSource) return;
    setBusy("scan"); setNotice(null); setError(null);
    try {
      const result = await adminMediaApi.scanExternalSource(selectedSource.id);
      setNotice(`扫描完成：发现 ${result.discovered_count} 个视频，新增 ${result.added_count} 个，变更 ${result.changed_count} 个，本次缺失 ${result.missing_count} 个。`);
      await Promise.all([loadSources(), loadEntries(selectedSource.id, parent)]);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "共享目录扫描失败，上次结果已保留"); }
    finally { setBusy(null); }
  }

  async function enqueueSelected() {
    if (!selectedSource || selected.length === 0) return;
    setBusy("enqueue"); setNotice(null); setError(null);
    try {
      const result = await adminMediaApi.enqueueExternal(selectedSource.id, selected);
      setNotice(`已加入 ${result.enqueued} 个转录任务${result.failed ? `，${result.failed} 个失败` : ""}。`);
      await Promise.all([loadEntries(selectedSource.id, parent), onMediaChanged()]);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "批量加入转录队列失败"); }
    finally { setBusy(null); }
  }

  async function enqueueAll() {
    if (!selectedSource || !window.confirm(`确认将“${selectedSource.name}”中尚未创建任务的可用视频批量加入转录队列吗？`)) return;
    setBusy("enqueue"); setNotice(null); setError(null);
    try {
      const result = await adminMediaApi.enqueueExternal(selectedSource.id);
      setNotice(`本批已加入 ${result.enqueued} 个转录任务${result.failed ? `，${result.failed} 个失败` : ""}${result.requested >= 500 ? "。如仍有待处理视频，可再次执行" : ""}。`);
      await Promise.all([loadEntries(selectedSource.id, parent), onMediaChanged()]);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "批量加入转录队列失败"); }
    finally { setBusy(null); }
  }

  async function toggleSource() {
    if (!selectedSource) return;
    setBusy("save"); setNotice(null); setError(null);
    try {
      await adminMediaApi.updateExternalSource(selectedSource.id, {
        name: selectedSource.name, target_category_id: selectedSource.target_category_id,
        default_scheme_id: selectedSource.default_scheme_id, auto_enqueue: selectedSource.auto_enqueue,
        scan_interval_seconds: selectedSource.scan_interval_seconds, enabled: !selectedSource.enabled,
        expected_version: selectedSource.version,
      });
      setNotice(selectedSource.enabled ? "已暂停该资料源的周期扫描。" : "已恢复该资料源的周期扫描。");
      await loadSources();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "资料源状态更新失败"); }
    finally { setBusy(null); }
  }

  const allSelected = selectable.length > 0 && selectable.every((entry) => selectedSet.has(entry.id));
  const openCreate = () => {
    setForm((current) => ({ ...current, rootAlias: current.rootAlias || roots[0]?.alias || "" }));
    setDialogOpen(true);
  };
  const parentUp = parent.includes("/") ? parent.slice(0, parent.lastIndexOf("/")) : "";

  return <>
    <Card className="overflow-hidden shadow-surface" aria-labelledby="external-media-title">
      <div className="flex flex-col gap-3 border-b border-border px-4 py-4 sm:px-5 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0"><div className="flex items-center gap-2"><Network className="size-5 text-primary" aria-hidden="true" /><h2 id="external-media-title" className="text-ui-base font-semibold">共享资料源</h2></div><p className="mt-1 text-ui-xs text-muted-foreground">远程原视频保持只读；本系统保存扫描身份、转录稿和独立的审核、发布、索引状态。</p></div>
        <div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" aria-label="刷新共享资料源" onClick={() => void loadSources()} disabled={loading}><RefreshCw className="size-4" />刷新共享资料源</Button></div>
      </div>
      {roots.length === 0 && !loading && <Alert className="m-4" role="status"><AlertTitle>未配置共享目录根</AlertTitle><AlertDescription>服务端尚未配置可选根别名，当前功能保持关闭，不影响本地上传和现有媒体。</AlertDescription></Alert>}
      {error && <Alert className="m-4" variant="destructive" role="alert"><AlertTitle>共享资料源操作失败</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
      {notice && <Alert className="m-4" role="status"><AlertTitle>操作完成</AlertTitle><AlertDescription>{notice}</AlertDescription></Alert>}
      {loading ? <LoadingState className="min-h-40" label="正在加载共享资料源…" /> : sources.length === 0 ? <EmptyState title="暂无共享资料源" description="由管理员选择服务端白名单中的根别名，并登记其下的相对目录。" /> : <div className="grid min-h-80 lg:grid-cols-[17rem_minmax(0,1fr)]">
        <nav className="border-b border-border p-3 lg:border-b-0 lg:border-r" aria-label="共享资料源列表"><ul className="space-y-1">{sources.map((source) => { const status = sourceStatus[source.status]; return <li key={source.id}><button type="button" className={`w-full rounded-ui-sm px-3 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${selectedSourceId === source.id ? "bg-primary/10" : "hover:bg-surface-muted"}`} onClick={() => setSelectedSourceId(source.id)}><span className="flex items-center justify-between gap-2"><span className="min-w-0 truncate text-ui-sm font-medium" title={source.name}>{source.name}</span><Badge variant={status[1]}>{status[0]}</Badge></span><span className="mt-1 block text-ui-xs text-muted-foreground">{source.available_files} 可用 · {source.missing_files} 缺失</span></button></li>; })}</ul></nav>
        <div className="min-w-0">
          {selectedSource && <div className="border-b border-border px-4 py-3 sm:px-5"><div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"><div className="min-w-0"><h3 className="font-semibold">{selectedSource.name}</h3><p className="mt-1 break-words text-ui-xs text-muted-foreground">根别名：{selectedSource.root_alias}{selectedSource.relative_path ? ` / ${selectedSource.relative_path}` : ""} · {selectedSource.last_successful_scan_at ? `最近成功 ${formatAdminDate(selectedSource.last_successful_scan_at)}` : "尚未成功扫描"}</p></div><div className="flex flex-wrap gap-2"><Button size="sm" variant="ghost" disabled={busy != null} onClick={() => void toggleSource()}>{selectedSource.enabled ? "暂停周期扫描" : "恢复周期扫描"}</Button><Button size="sm" variant="outline" disabled={busy != null} onClick={() => void scan()}><FolderSync className="size-4" />{busy === "scan" ? "扫描中…" : "立即扫描"}</Button><Button size="sm" variant="outline" disabled={busy != null || selectedSource.available_files === 0} onClick={() => void enqueueAll()}>全部待转录视频</Button><Button size="sm" disabled={busy != null || selected.length === 0} onClick={() => void enqueueSelected()}>{busy === "enqueue" ? "加入中…" : `加入转录（${selected.length}）`}</Button></div></div></div>}
          <div className="flex items-center gap-2 border-b border-border px-4 py-2 text-ui-xs sm:px-5"><Button size="sm" variant="ghost" disabled={!parent} onClick={() => setParent(parentUp)}><ChevronLeft className="size-4" />上一级</Button><span className="min-w-0 truncate text-muted-foreground" title={parent || "根目录"}>{parent || "根目录"}</span></div>
          {entriesLoading ? <LoadingState className="min-h-40" label="正在读取目录…" /> : entries.length === 0 ? <EmptyState title="当前目录没有视频" description="执行扫描后，此处会显示支持的视频和子文件夹。" /> : <><div className="flex items-center gap-2 border-b border-border px-4 py-2 sm:px-5"><Checkbox aria-label="全选当前目录可入队视频" checked={allSelected} onChange={(event) => setSelected(event.target.checked ? selectable.map((entry) => entry.id) : [])} /><span className="text-ui-xs text-muted-foreground">当前目录 {videos.length} 个视频，{selectable.length} 个可加入转录</span></div><ul className="divide-y divide-border" aria-label="共享目录内容">{entries.map((entry) => <li key={entry.id} className="flex flex-col gap-3 px-4 py-3 sm:px-5 md:flex-row md:items-center">
            <div className="flex min-w-0 flex-1 items-start gap-3">{entry.kind === "video" ? <Checkbox className="mt-0.5" aria-label={`选择 ${entry.name}`} checked={selectedSet.has(entry.id)} disabled={entry.availability !== "available" || Boolean(entry.transcription_job_id)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, entry.id] : current.filter((id) => id !== entry.id))} /> : <Folder className="mt-0.5 size-4 text-primary" aria-hidden="true" />}<button type="button" className="min-w-0 text-left" onClick={() => entry.kind === "folder" && setParent(entry.relative_path)} disabled={entry.kind !== "folder"}><span className="block truncate text-ui-sm font-medium" title={entry.name}>{entry.name}</span><span className="mt-1 block text-ui-xs text-muted-foreground">{entry.kind === "folder" ? "共享文件夹" : `${formatBytes(entry.file_size || 0)} · ${entry.availability === "missing" ? "远程文件缺失" : workflowLabel[entry.index_status || entry.publication_status || entry.review_status || entry.transcription_job_status || ""] || "尚未转录"}`}</span></button></div>
            {entry.kind === "video" && <div className="flex flex-wrap items-center gap-2 md:justify-end">{entry.availability === "missing" && <Badge variant="destructive">不可用</Badge>}{entry.transcription_job_status && <Badge variant={entry.transcription_job_status === "failed" ? "destructive" : entry.transcription_job_status === "succeeded" ? "success" : "warning"}>{workflowLabel[entry.transcription_job_status] || entry.transcription_job_status}</Badge>}{entry.media_id && entry.availability === "available" && <Button size="sm" variant="outline" onClick={() => onOpenWorkbench(entry.media_id!)}><Play className="size-4" />预览与转写</Button>}</div>}
          </li>)}</ul></>}
        </div>
      </div>}
    </Card>

    <Dialog open={dialogOpen} onOpenChange={(open) => { if (!busy) setDialogOpen(open); }}><DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto"><DialogHeader><DialogTitle>登记共享目录</DialogTitle><DialogDescription>这里只选择服务端白名单根别名及其下相对路径，不保存网络凭据，也不会修改远程文件。</DialogDescription></DialogHeader><div className="space-y-4"><label className="block text-ui-sm font-medium">显示名称<Input className="mt-1" value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} placeholder="例如：培训视频归档" /></label><div className="grid gap-4 sm:grid-cols-2"><label className="block text-ui-sm font-medium">共享根别名<Select className="mt-1" value={form.rootAlias} onChange={(event) => setForm((current) => ({ ...current, rootAlias: event.target.value }))}><option value="">请选择</option>{roots.map((root) => <option key={root.alias} value={root.alias}>{root.alias}</option>)}</Select></label><label className="block text-ui-sm font-medium">相对目录<Input className="mt-1" value={form.relativePath} onChange={(event) => setForm((current) => ({ ...current, relativePath: event.target.value }))} placeholder="可留空，示例：2026/培训" /></label></div><CategoryTreePicker categories={categories} value={form.categoryId} onChange={(categoryId) => setForm((current) => ({ ...current, categoryId }))} label="转录稿发布目录" /><label className="block text-ui-sm font-medium">默认转录方案<Select className="mt-1" value={form.schemeId} onChange={(event) => setForm((current) => ({ ...current, schemeId: event.target.value }))}><option value="">请选择</option>{schemes.map((scheme) => <option key={scheme.scheme_id} value={scheme.scheme_id} disabled={!scheme.enabled || scheme.archived || scheme.availability !== "available"}>{scheme.name}{scheme.availability !== "available" ? "（不可用）" : ""}</option>)}</Select></label><label className="flex items-start gap-2 text-ui-sm"><Checkbox checked={form.autoEnqueue} onChange={(event) => setForm((current) => ({ ...current, autoEnqueue: event.target.checked }))} /><span>后续扫描发现的新视频自动加入转录队列。首次扫描仍需人工确认批量入队。</span></label><label className="block text-ui-sm font-medium">扫描间隔（秒）<Input className="mt-1" type="number" min={60} max={86400} value={form.interval} onChange={(event) => setForm((current) => ({ ...current, interval: event.target.value }))} /></label></div><DialogFooter><Button variant="outline" disabled={busy != null} onClick={() => setDialogOpen(false)}>取消</Button><Button disabled={busy != null || !form.name.trim() || !form.rootAlias || !form.categoryId || !form.schemeId || Number(form.interval) < 60} onClick={() => void createSource()}>{busy === "save" ? "登记中…" : "登记共享目录"}</Button></DialogFooter></DialogContent></Dialog>
  </>;
}
