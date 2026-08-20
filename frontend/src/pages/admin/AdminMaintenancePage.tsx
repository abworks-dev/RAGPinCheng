import { useEffect, useState } from "react";
import { RefreshCw, Save, Trash2 } from "lucide-react";
import { adminMaintenanceApi } from "../../api/admin/maintenance";
import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "../../components/ui/dialog";
import { ErrorState } from "../../components/ui/error-state";
import { Input } from "../../components/ui/input";
import { LoadingState } from "../../components/ui/loading-state";
import { Select } from "../../components/ui/select";
import type { CleanupPreview, MaintenanceRun, MaintenanceSettings, MaintenanceStatus } from "../../types";
import { formatAdminDate } from "../../lib/admin-formatters";

const PRESETS = [30, 90, 180, 365];
const NEVER = "never";
const retentionLabel = (days: number | null) => days === null ? "永久保留" : `保留 ${days} 天`;

function runDuration(startedAt: number, finishedAt: number) {
  const milliseconds = (finishedAt - startedAt) * 1000;
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "耗时未知";
  return milliseconds < 1000 ? `耗时 ${milliseconds} 毫秒` : `耗时 ${(milliseconds / 1000).toFixed(1)} 秒`;
}

function runError(run: MaintenanceRun) {
  if (run.status !== "failed") return null;
  return run.error_summary || "执行失败，未记录具体原因";
}

function countSummary(preview: CleanupPreview) {
  return `${preview.conversations} 条对话、${preview.messages} 条消息、${preview.auth_sessions} 个失效登录会话`;
}

export function AdminMaintenancePage() {
  const [status, setStatus] = useState<MaintenanceStatus | null>(null);
  const [preview, setPreview] = useState<CleanupPreview | null>(null);
  const [runs, setRuns] = useState<MaintenanceRun[]>([]);
  const [enabled, setEnabled] = useState(true);
  const [days, setDays] = useState("30");
  const [customDays, setCustomDays] = useState("30");
  const [maxFileMb, setMaxFileMb] = useState("2000");
  const [maxBatchFiles, setMaxBatchFiles] = useState("5000");
  const [maxBatchMb, setMaxBatchMb] = useState("10240");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<"load" | "preview" | "save" | "cleanup" | null>("load");
  const [confirmSettings, setConfirmSettings] = useState<MaintenanceSettings | null>(null);
  const [cleanupOpen, setCleanupOpen] = useState(false);

  const selectedDays = days === NEVER ? null : Number(days === "custom" ? customDays : days);
  const validDays = selectedDays === null || (Number.isInteger(selectedDays) && selectedDays >= 7 && selectedDays <= 3650);

  async function load() {
    setBusy("load");
    setError(null);
    try {
      const [nextStatus, nextPreview, nextRuns] = await Promise.all([
        adminMaintenanceApi.status(), adminMaintenanceApi.preview(), adminMaintenanceApi.runs(),
      ]);
      setStatus(nextStatus);
      setPreview(nextPreview);
      setRuns(nextRuns.runs);
      setEnabled(nextStatus.settings.conversation_cleanup_enabled);
      const retention = nextStatus.settings.conversation_retention_days;
      setDays(retention === null ? NEVER : PRESETS.includes(retention) ? String(retention) : "custom");
      setCustomDays(retention === null ? "30" : String(retention));
      setMaxFileMb(String(nextStatus.settings.upload_max_file_mb));
      setMaxBatchFiles(String(nextStatus.settings.upload_max_batch_files));
      setMaxBatchMb(String(nextStatus.settings.upload_max_batch_mb));
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => { void load(); }, []);

  async function refreshPreview(retentionDays?: number) {
    if (retentionDays !== undefined && (!Number.isInteger(retentionDays) || retentionDays < 7 || retentionDays > 3650)) return null;
    setBusy("preview");
    try {
      const value = await adminMaintenanceApi.preview(retentionDays);
      setPreview(value);
      return value;
    } catch (e: any) {
      setNotice(e?.message || String(e));
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function requestSave() {
    if (!status || !validDays) return;
    const candidate: MaintenanceSettings = {
      conversation_cleanup_enabled: enabled,
      conversation_retention_days: selectedDays,
      upload_max_file_mb: status.settings.upload_max_file_mb,
      upload_max_batch_files: status.settings.upload_max_batch_files,
      upload_max_batch_mb: status.settings.upload_max_batch_mb,
      updated_at: status.settings.updated_at,
      updated_by: status.settings.updated_by,
    };
    if ((selectedDays !== null && (status.settings.conversation_retention_days === null || selectedDays < status.settings.conversation_retention_days)) || !enabled) {
      const candidatePreview = await refreshPreview(selectedDays === null ? undefined : selectedDays);
      if (!candidatePreview) return;
      setConfirmSettings(candidate);
      return;
    }
    await save(candidate);
  }

  async function save(candidate: MaintenanceSettings) {
    setBusy("save");
    setNotice(null);
    try {
      const settings = await adminMaintenanceApi.updateSettings(candidate);
      setStatus((current) => current ? { ...current, settings } : current);
      setConfirmSettings(null);
      setNotice("维护策略已保存，将从下一次自动检查开始生效。");
      await refreshPreview(settings.conversation_retention_days ?? undefined);
    } catch (e: any) {
      setNotice(e?.message || String(e));
    } finally {
      setBusy(null);
    }
  }

  async function saveUploadLimits() {
    if (!status) return;
    const candidate: MaintenanceSettings = {
      ...status.settings,
      upload_max_file_mb: Number(maxFileMb),
      upload_max_batch_files: Number(maxBatchFiles),
      upload_max_batch_mb: Number(maxBatchMb),
    };
    setBusy("save");
    setNotice(null);
    try {
      const settings = await adminMaintenanceApi.updateSettings(candidate);
      setStatus((current) => current ? { ...current, settings } : current);
      setMaxFileMb(String(settings.upload_max_file_mb));
      setMaxBatchFiles(String(settings.upload_max_batch_files));
      setMaxBatchMb(String(settings.upload_max_batch_mb));
      setNotice("资料上传限制已保存，新上传请求将立即使用当前设置。");
    } catch (e: any) {
      setNotice(e?.message || String(e));
    } finally {
      setBusy(null);
    }
  }

  async function cleanup() {
    setBusy("cleanup");
    setNotice(null);
    try {
      const result = await adminMaintenanceApi.cleanup();
      setCleanupOpen(false);
      setNotice(`清理完成：删除 ${result.deleted_conversations} 条对话、${result.deleted_messages} 条消息、${result.deleted_auth_sessions} 个失效登录会话。`);
      const [nextStatus, nextPreview, nextRuns] = await Promise.all([
        adminMaintenanceApi.status(), adminMaintenanceApi.preview(), adminMaintenanceApi.runs(),
      ]);
      setStatus(nextStatus);
      setPreview(nextPreview);
      setRuns(nextRuns.runs);
    } catch (e: any) {
      setNotice(e?.message || String(e));
    } finally {
      setBusy(null);
    }
  }

  if (busy === "load" && !status) return <LoadingState className="min-h-72" label="正在加载系统维护状态…" />;
  if (error || !status) return <ErrorState title="系统维护加载失败" description={error || "暂无可用状态"} action={<Button variant="outline" onClick={() => void load()}>重试</Button>} />;

  const dirty = enabled !== status.settings.conversation_cleanup_enabled || selectedDays !== status.settings.conversation_retention_days;
  const uploadValues = [Number(maxFileMb), Number(maxBatchFiles), Number(maxBatchMb)];
  const uploadValid = uploadValues.every(Number.isInteger)
    && uploadValues[0] >= 1 && uploadValues[0] <= 10240
    && uploadValues[1] >= 1 && uploadValues[1] <= 10000
    && uploadValues[2] >= uploadValues[0] && uploadValues[2] <= 102400;
  const uploadDirty = uploadValues[0] !== status.settings.upload_max_file_mb
    || uploadValues[1] !== status.settings.upload_max_batch_files
    || uploadValues[2] !== status.settings.upload_max_batch_mb;
  const lastRun = status.last_run;

  return (
    <div className="space-y-6" aria-labelledby="maintenance-title">
      <div>
        <p className="text-ui-xs font-medium text-primary">总览</p>
        <h1 id="maintenance-title" className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">系统维护</h1>
        <p className="mt-2 max-w-2xl text-ui-sm text-muted-foreground">配置历史对话保留策略，检查影响范围并追踪清理结果。</p>
      </div>

      {notice && <Alert variant={notice.includes("失败") ? "destructive" : "success"} aria-live="polite"><AlertTitle>操作结果</AlertTitle><AlertDescription>{notice}</AlertDescription></Alert>}

      <section aria-labelledby="maintenance-status" className="space-y-3">
        <h2 id="maintenance-status" className="text-ui-base font-semibold">运行状态</h2>
        <dl className="grid grid-cols-1 gap-px overflow-hidden rounded-ui-xl border border-border bg-border sm:grid-cols-2 xl:grid-cols-4">
          <div className="bg-card p-4"><dt className="text-ui-xs text-muted-foreground">自动清理</dt><dd className="mt-2"><Badge variant={status.settings.conversation_cleanup_enabled ? "success" : "warning"}>{status.settings.conversation_cleanup_enabled ? "已启用" : "已停用"}</Badge></dd></div>
          <div className="bg-card p-4"><dt className="text-ui-xs text-muted-foreground">当前保留期</dt><dd className="mt-2 text-ui-lg font-semibold">{retentionLabel(status.settings.conversation_retention_days)}</dd></div>
          <div className="bg-card p-4"><dt className="text-ui-xs text-muted-foreground">检查频率</dt><dd className="mt-2 text-ui-sm font-medium">每 {status.sweeper_interval_seconds / 3600} 小时</dd></div>
          <div className="bg-card p-4"><dt className="text-ui-xs text-muted-foreground">最近运行</dt><dd className="mt-2 text-ui-sm font-medium">{lastRun ? formatAdminDate(lastRun.finished_at) : "尚无记录"}</dd></div>
        </dl>
      </section>

      <Card>
        <CardHeader><CardTitle>历史对话保留策略</CardTitle><CardDescription>期限按对话最后活动时间计算。失效登录会话始终按认证到期时间清理。</CardDescription></CardHeader>
        <CardContent className="space-y-5">
          <label className="flex items-start gap-3 rounded-ui-lg border border-border p-4">
            <Checkbox checked={enabled} onChange={(event) => setEnabled(event.target.checked)} disabled={busy === "save"} />
            <span><span className="block text-ui-sm font-medium">启用历史对话自动清理</span><span className="mt-1 block text-ui-xs text-muted-foreground">停用后不会自动删除历史对话，但失效登录会话仍会清理。</span></span>
          </label>
          <div className="grid gap-4 sm:grid-cols-[minmax(0,14rem)_minmax(0,14rem)_auto] sm:items-end">
            <label className="space-y-1.5 text-ui-sm font-medium"><span>保留期限</span><Select value={days} onChange={(event) => setDays(event.target.value)} disabled={busy === "save"}><option value={NEVER}>永久保留（不清理）</option>{PRESETS.map((value) => <option key={value} value={value}>{value} 天</option>)}<option value="custom">自定义</option></Select></label>
            {days === "custom" && <label className="space-y-1.5 text-ui-sm font-medium"><span>自定义天数</span><Input type="number" min={7} max={3650} value={customDays} onChange={(event) => setCustomDays(event.target.value)} aria-describedby="retention-help" /></label>}
            <Button onClick={() => void requestSave()} disabled={!dirty || !validDays || busy !== null}><Save className="size-4" />{busy === "save" ? "保存中…" : "保存策略"}</Button>
          </div>
          <p id="retention-help" className={validDays ? "text-ui-xs text-muted-foreground" : "text-ui-xs text-destructive"}>{validDays ? "可设置 7 至 3650 天，或选择永久保留；默认值为 30 天。保存设置不会立即删除数据。" : "请输入 7 至 3650 之间的整数。"}</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>资料上传限制</CardTitle><CardDescription>限制受管资料的新建、更新和文件夹上传。保存后立即作用于新请求。</CardDescription></CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-3">
            <label className="space-y-1.5 text-ui-sm font-medium"><span>单文件上限（MB）</span><Input type="number" min={1} max={10240} value={maxFileMb} onChange={(event) => setMaxFileMb(event.target.value)} disabled={busy === "save"} /></label>
            <label className="space-y-1.5 text-ui-sm font-medium"><span>单批文件数量</span><Input type="number" min={1} max={10000} value={maxBatchFiles} onChange={(event) => setMaxBatchFiles(event.target.value)} disabled={busy === "save"} /></label>
            <label className="space-y-1.5 text-ui-sm font-medium"><span>单批总大小（MB）</span><Input type="number" min={1} max={102400} value={maxBatchMb} onChange={(event) => setMaxBatchMb(event.target.value)} disabled={busy === "save"} /></label>
          </div>
          <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className={uploadValid ? "text-ui-xs text-muted-foreground" : "text-ui-xs text-destructive"}>{uploadValid ? "单文件最大 10240 MB，单批最多 10000 个文件；单批总大小不得小于单文件上限。" : "请输入有效整数，且单批总大小不得小于单文件上限。"}</p>
            <Button onClick={() => void saveUploadLimits()} disabled={!uploadDirty || !uploadValid || busy !== null}><Save className="size-4" />{busy === "save" ? "保存中…" : "保存上传限制"}</Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>立即清理</CardTitle><CardDescription>预览仅统计数据，不执行删除。手动清理始终采用当前已保存的策略。</CardDescription></CardHeader>
        <CardContent className="space-y-4">
          {preview && <div className="grid grid-cols-2 gap-4 border-y border-border py-4 sm:grid-cols-4"><div><p className="text-ui-xs text-muted-foreground">待清理对话</p><p className="mt-1 text-ui-xl font-semibold tabular-nums">{preview.conversations}</p></div><div><p className="text-ui-xs text-muted-foreground">关联消息</p><p className="mt-1 text-ui-xl font-semibold tabular-nums">{preview.messages}</p></div><div><p className="text-ui-xs text-muted-foreground">失效登录会话</p><p className="mt-1 text-ui-xl font-semibold tabular-nums">{preview.auth_sessions}</p></div><div><p className="text-ui-xs text-muted-foreground">最近待清理时间</p><p className="mt-1 text-ui-sm font-medium">{formatAdminDate(preview.newest_conversation_at)}</p></div></div>}
          <div className="flex flex-col gap-2 sm:flex-row"><Button variant="outline" onClick={() => void refreshPreview(status.settings.conversation_retention_days ?? undefined)} disabled={busy !== null}><RefreshCw className="size-4" />{busy === "preview" ? "刷新中…" : "刷新预览"}</Button><Button variant="destructive" onClick={() => setCleanupOpen(true)} disabled={busy !== null || !preview}><Trash2 className="size-4" />立即执行清理</Button></div>
        </CardContent>
      </Card>

      <section aria-labelledby="maintenance-runs" className="space-y-3">
        <h2 id="maintenance-runs" className="text-ui-base font-semibold">最近运行记录</h2>
        {runs.length === 0 ? <p className="rounded-ui-xl border border-border px-4 py-8 text-center text-ui-sm text-muted-foreground">尚无清理记录</p> : <>
          <div className="hidden overflow-hidden rounded-ui-xl border border-border md:block"><table className="w-full text-ui-sm"><thead className="bg-surface-muted text-left text-muted-foreground"><tr><th className="px-4 py-3 font-medium">运行时间</th><th className="px-4 py-3 font-medium">方式</th><th className="px-4 py-3 font-medium">策略</th><th className="px-4 py-3 font-medium">清理结果</th><th className="px-4 py-3 font-medium">状态</th></tr></thead><tbody className="divide-y divide-border">{runs.map((run) => <tr key={run.id}><td className="px-4 py-3"><span className="block">{formatAdminDate(run.finished_at)}</span><span className="mt-1 block text-ui-xs text-muted-foreground">{runDuration(run.started_at, run.finished_at)}</span></td><td className="px-4 py-3">{run.trigger_source === "automatic" ? "自动" : "手动"}</td><td className="px-4 py-3">{retentionLabel(run.retention_days)}</td><td className="px-4 py-3"><span className="block">{run.deleted_conversations} 条对话 · {run.deleted_messages} 条消息 · {run.deleted_auth_sessions} 个登录会话</span>{runError(run) && <span className="mt-1 block max-w-xl break-words text-ui-xs text-destructive">{runError(run)}</span>}</td><td className="px-4 py-3"><Badge variant={run.status === "succeeded" ? "success" : "destructive"}>{run.status === "succeeded" ? "成功" : "失败"}</Badge></td></tr>)}</tbody></table></div>
          <ul className="divide-y divide-border rounded-ui-xl border border-border md:hidden">{runs.map((run) => <li key={run.id} className="space-y-2 p-4"><div className="flex items-center justify-between gap-3"><span className="text-ui-sm font-medium">{run.trigger_source === "automatic" ? "自动清理" : "手动清理"}</span><Badge variant={run.status === "succeeded" ? "success" : "destructive"}>{run.status === "succeeded" ? "成功" : "失败"}</Badge></div><p className="text-ui-xs text-muted-foreground">{formatAdminDate(run.finished_at)} · {runDuration(run.started_at, run.finished_at)} · {retentionLabel(run.retention_days)}</p><p className="text-ui-sm">{run.deleted_conversations} 条对话 · {run.deleted_messages} 条消息 · {run.deleted_auth_sessions} 个登录会话</p>{runError(run) && <p className="break-words text-ui-xs text-destructive">{runError(run)}</p>}</li>)}</ul>
        </>}
      </section>

      <Dialog open={Boolean(confirmSettings)} onOpenChange={(open) => { if (!open && busy !== "save") setConfirmSettings(null); }}><DialogContent><DialogHeader><DialogTitle>{confirmSettings?.conversation_cleanup_enabled ? "确认缩短保留期限" : "确认停用自动清理"}</DialogTitle><DialogDescription>{confirmSettings?.conversation_cleanup_enabled ? `新策略为${retentionLabel(confirmSettings.conversation_retention_days)}。下一次自动检查可能删除当前预估的 ${preview ? countSummary(preview) : "过期数据"}。` : "停用后历史对话会持续保留并增加存储占用；失效登录会话不受影响。"}</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={() => setConfirmSettings(null)} disabled={busy === "save"}>取消</Button><Button variant={confirmSettings?.conversation_cleanup_enabled ? "destructive" : "default"} onClick={() => confirmSettings && void save(confirmSettings)} disabled={busy === "save"}>{busy === "save" ? "保存中…" : "确认保存"}</Button></DialogFooter></DialogContent></Dialog>

      <Dialog open={cleanupOpen} onOpenChange={(open) => { if (busy !== "cleanup") setCleanupOpen(open); }}><DialogContent><DialogHeader><DialogTitle>确认立即清理过期数据</DialogTitle><DialogDescription>将按已保存的 {retentionLabel(status.settings.conversation_retention_days)} 策略永久删除当前预估的 {preview ? countSummary(preview) : "过期数据"}。删除后只能通过数据库备份恢复。</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={() => setCleanupOpen(false)} disabled={busy === "cleanup"}>取消</Button><Button variant="destructive" onClick={() => void cleanup()} disabled={busy === "cleanup"}>{busy === "cleanup" ? "清理中…" : "确认永久删除"}</Button></DialogFooter></DialogContent></Dialog>
    </div>
  );
}
