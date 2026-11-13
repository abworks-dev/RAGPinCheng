import { useCallback, useEffect, useMemo, useState } from "react";
import { ArchiveRestore, Play, RefreshCw } from "lucide-react";
import { adminSharedSyncApi } from "../../api/admin/sharedSync";
import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { LoadingState } from "../../components/ui/loading-state";
import { Select } from "../../components/ui/select";
import { toast } from "../../components/ui/toast";
import { formatAdminDate } from "../../lib/admin-formatters";
import type {
  SharedSyncEvent,
  SharedSyncEventAction,
  SharedSyncPhase,
  SharedSyncRun,
  SharedSyncStatus,
  SharedSyncWithdrawal,
  SharedSyncWithdrawalReason,
  SharedSyncWithdrawalState,
} from "../../types";

const phaseLabel: Record<SharedSyncPhase, string> = {
  day: "日间（只登记、不自动发布）",
  quiet: "缓冲期（只扫描）",
  window: "夜间窗口（扫描、导入并自动发布）",
};

const phaseHint: Record<SharedSyncPhase, string> = {
  day: "共享目录的新增和改动只会登记为待发布，需在夜间窗口或由管理员手动发布。",
  quiet: "已进入夜间缓冲期，本阶段只做扫描登记，不做导入和发布。",
  window: "正在夜间窗口内：稳定文件会被导入并自动发布，确认仍缺失的资料会在当夜清理知识库副本。",
};

const actionLabel: Record<SharedSyncEventAction, string> = {
  observed: "观测到稳定版本",
  coalesced: "被更新的版本取代",
  skipped: "已跳过",
  imported: "已导入",
  published: "已提交发布",
  withdrawn: "已撤回",
  restored: "已恢复",
  purged: "已清理",
  failed: "失败",
};

const actionVariant: Record<SharedSyncEventAction, "default" | "secondary" | "outline" | "success" | "warning" | "destructive" | "info"> = {
  observed: "outline",
  coalesced: "outline",
  skipped: "outline",
  imported: "info",
  published: "success",
  withdrawn: "warning",
  restored: "success",
  purged: "destructive",
  failed: "destructive",
};

const reasonLabel: Record<SharedSyncWithdrawalReason, string> = {
  shared_missing: "共享端文件缺失",
  shared_replaced: "共享端文件被替换",
  manual: "管理员手动撤回",
};

const stateLabel: Record<SharedSyncWithdrawalState, string> = {
  withdrawn: "已暂撤，可恢复",
  restored: "已恢复",
  cleanup_claimed: "清理中，不可恢复",
  purged: "已清理",
};

const stateVariant: Record<SharedSyncWithdrawalState, "warning" | "success" | "info" | "destructive"> = {
  withdrawn: "warning",
  restored: "success",
  cleanup_claimed: "info",
  purged: "destructive",
};

const runStatusLabel: Record<SharedSyncRun["status"], string> = {
  running: "同步中",
  succeeded: "成功",
  failed: "失败",
  skipped: "已跳过",
};

const runStatusVariant: Record<SharedSyncRun["status"], "info" | "success" | "destructive" | "outline"> = {
  running: "info",
  succeeded: "success",
  failed: "destructive",
  skipped: "outline",
};

const triggerLabel: Record<SharedSyncRun["trigger"], string> = {
  manual: "手动触发",
  nightly: "夜间自动",
};

function runDuration(run: SharedSyncRun) {
  if (!run.finished_at) return "进行中";
  const seconds = run.finished_at - run.started_at;
  if (!Number.isFinite(seconds) || seconds < 0) return "耗时未知";
  return seconds < 60 ? `${seconds} 秒` : `${(seconds / 60).toFixed(1)} 分钟`;
}

function runSummary(run: SharedSyncRun) {
  const c = run.counts;
  const parts = [
    c.discovered_count ? `发现 ${c.discovered_count}` : "",
    c.added_count ? `新增 ${c.added_count}` : "",
    c.changed_count ? `变更 ${c.changed_count}` : "",
    c.missing_count ? `缺失 ${c.missing_count}` : "",
    c.imported_count ? `导入 ${c.imported_count}` : "",
    c.published_count ? `发布 ${c.published_count}` : "",
    c.withdrawn_count ? `撤回 ${c.withdrawn_count}` : "",
    c.restored_count ? `恢复 ${c.restored_count}` : "",
    c.purged_count ? `清理 ${c.purged_count}` : "",
    c.failed_count ? `失败 ${c.failed_count}` : "",
  ].filter(Boolean);
  return parts.length > 0 ? parts.join("、") : "无变化";
}

export function AdminSharedSyncPage() {
  const [status, setStatus] = useState<SharedSyncStatus | null>(null);
  const [runs, setRuns] = useState<SharedSyncRun[]>([]);
  const [events, setEvents] = useState<SharedSyncEvent[]>([]);
  const [withdrawals, setWithdrawals] = useState<SharedSyncWithdrawal[]>([]);
  const [sourceFilter, setSourceFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"run" | string | null>(null);

  const refresh = useCallback(async (background = false) => {
    background ? setRefreshing(true) : setLoading(true);
    setError(null);
    try {
      const [nextStatus, nextRuns, nextEvents, nextWithdrawals] = await Promise.all([
        adminSharedSyncApi.status(),
        adminSharedSyncApi.runs({ sourceId: sourceFilter || undefined, limit: 20 }),
        adminSharedSyncApi.events({ sourceId: sourceFilter || undefined, limit: 50 }),
        adminSharedSyncApi.withdrawals({ sourceId: sourceFilter || undefined, limit: 50 }),
      ]);
      setStatus(nextStatus);
      setRuns(nextRuns);
      setEvents(nextEvents);
      setWithdrawals(nextWithdrawals);
    } catch (caught: any) {
      setError(caught?.message || String(caught));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [sourceFilter]);

  useEffect(() => { void refresh(); }, [refresh]);

  const pendingWithdrawals = useMemo(
    () => withdrawals.filter((item) => item.state === "withdrawn"),
    [withdrawals],
  );

  async function runNow() {
    setBusy("run");
    try {
      const summary = await adminSharedSyncApi.runNow(sourceFilter ? [sourceFilter] : null);
      const processed = summary.results.length;
      toast.success(
        processed > 0
          ? `本次同步已执行 ${processed} 个共享源（${phaseLabel[summary.phase]}）。`
          : "当前没有到期的共享源需要同步。",
      );
      await refresh(true);
    } catch (caught: any) {
      toast.error(caught?.message || "同步执行失败");
    } finally {
      setBusy(null);
    }
  }

  async function restore(item: SharedSyncWithdrawal) {
    setBusy(item.id);
    try {
      await adminSharedSyncApi.restore(item.id);
      toast.success("已恢复为该资料的当前版本。");
      await refresh(true);
    } catch (caught: any) {
      toast.error(caught?.message || "恢复失败");
      await refresh(true);
    } finally {
      setBusy(null);
    }
  }

  if (loading && !status) return <LoadingState className="min-h-72" label="正在加载共享同步状态…" />;
  if (error || !status) {
    return (
      <ErrorState
        title="共享同步状态加载失败"
        description={error || "暂无可用状态"}
        action={<Button variant="outline" onClick={() => void refresh()}>重试</Button>}
      />
    );
  }

  return (
    <div className="admin-page admin-settings space-y-5" aria-labelledby="shared-sync-title">
      <div className="admin-page-heading">
        <p className="text-ui-xs font-medium text-primary">内容管理</p>
        <h1 id="shared-sync-title" className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">共享目录同步</h1>
        <p className="mt-2 max-w-2xl text-ui-sm text-muted-foreground">
          查看共享目录的扫描、发布、撤回与恢复记录。同步只读取共享目录，不会改动共享上的文件。
        </p>
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <CardTitle>当前状态</CardTitle>
              <CardDescription>{phaseHint[status.phase]}</CardDescription>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="outline" onClick={() => void refresh(true)} disabled={refreshing || busy !== null}>
                <RefreshCw className={refreshing ? "size-4 animate-spin" : "size-4"} aria-hidden="true" />
                刷新
              </Button>
              <Button onClick={() => void runNow()} disabled={busy !== null}>
                <Play className="size-4" aria-hidden="true" />
                {busy === "run" ? "正在同步…" : "立即同步一次"}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {!status.enabled && (
            <Alert>
              <AlertTitle>自动同步未启用</AlertTitle>
              <AlertDescription>
                当前部署关闭了共享目录自动同步（SHARED_SYNC_ENABLED=false）。下方“立即同步一次”仍可用于人工核对，
                但夜间窗口不会自动运行。
              </AlertDescription>
            </Alert>
          )}
          <dl className="grid gap-3 sm:grid-cols-3">
            <div>
              <dt className="text-ui-xs text-muted-foreground">当前阶段</dt>
              <dd className="mt-1 text-ui-sm font-medium">{(status.enabled ? "" : "（未启用）") + phaseLabel[status.phase]}</dd>
            </div>
            <div>
              <dt className="text-ui-xs text-muted-foreground">下次阶段切换</dt>
              <dd className="mt-1 text-ui-sm font-medium">{formatAdminDate(status.next_transition)}</dd>
            </div>
            <div>
              <dt className="text-ui-xs text-muted-foreground">可恢复的撤回</dt>
              <dd className="mt-1 text-ui-sm font-medium">{pendingWithdrawals.length} 项</dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <CardTitle>共享源</CardTitle>
              <CardDescription>按来源统计条目状态与最近一次同步结果。</CardDescription>
            </div>
            <div className="min-w-48">
              <Select
                aria-label="按共享源筛选"
                value={sourceFilter}
                onChange={(event) => setSourceFilter(event.target.value)}
              >
                <option value="">全部共享源</option>
                {status.sources.map((source) => (
                  <option key={source.id} value={source.id}>{source.name}</option>
                ))}
              </Select>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {status.sources.length === 0 ? (
            <EmptyState title="还没有配置共享源" description="在转录配置或共享媒体来源中添加共享目录后，这里会显示其同步状态。" />
          ) : (
            <ul className="grid gap-3 lg:grid-cols-2">
              {status.sources.map((source) => (
                <li key={source.id} className="min-w-0 rounded-ui-lg border border-border px-4 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="min-w-0 truncate text-ui-sm font-medium text-foreground" title={source.name}>{source.name}</p>
                    <Badge variant={source.enabled ? "success" : "outline"}>{source.enabled ? "已启用" : "已停用"}</Badge>
                  </div>
                  <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-ui-xs sm:grid-cols-3">
                    <div><dt className="text-muted-foreground">条目</dt><dd className="mt-0.5 tabular-nums text-foreground">{source.state.entries_total ?? 0}</dd></div>
                    <div><dt className="text-muted-foreground">可用</dt><dd className="mt-0.5 tabular-nums text-foreground">{source.state.entries_available ?? 0}</dd></div>
                    <div><dt className="text-muted-foreground">缺失</dt><dd className="mt-0.5 tabular-nums text-foreground">{source.state.entries_missing ?? 0}</dd></div>
                    <div><dt className="text-muted-foreground">已导入</dt><dd className="mt-0.5 tabular-nums text-foreground">{source.state.imported ?? 0}</dd></div>
                    <div><dt className="text-muted-foreground">导入失败</dt><dd className="mt-0.5 tabular-nums text-foreground">{source.state.import_failed ?? 0}</dd></div>
                    <div><dt className="text-muted-foreground">撤回中</dt><dd className="mt-0.5 tabular-nums text-foreground">{source.state.withdrawn ?? 0}</dd></div>
                  </dl>
                  <p className="mt-3 text-ui-xs text-muted-foreground">
                    最近一次：{source.state.last_run
                      ? `${formatAdminDate(source.state.last_run.started_at)} · ${runStatusLabel[source.state.last_run.status]}`
                      : "尚未同步"}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>撤回与恢复</CardTitle>
          <CardDescription>
            共享端文件缺失或被替换时，旧版本会立即退出检索但保留恢复入口；凌晨清理确认后不可再恢复。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {withdrawals.length === 0 ? (
            <EmptyState title="没有撤回记录" description="共享端文件保持可用时不会产生撤回。" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[46rem] border-collapse text-left text-ui-sm">
                <thead>
                  <tr className="border-b border-border text-ui-xs text-muted-foreground">
                    <th scope="col" className="px-2 py-2 font-medium">文件</th>
                    <th scope="col" className="px-2 py-2 font-medium">原因</th>
                    <th scope="col" className="px-2 py-2 font-medium">状态</th>
                    <th scope="col" className="px-2 py-2 font-medium">撤回时间</th>
                    <th scope="col" className="px-2 py-2 font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {withdrawals.map((item) => (
                    <tr key={item.id} className="border-b border-border/60 align-top">
                      <td className="max-w-72 px-2 py-2">
                        <p className="truncate" title={item.relative_path || ""}>{item.relative_path || "未知路径"}</p>
                      </td>
                      <td className="px-2 py-2 text-muted-foreground">{reasonLabel[item.reason]}</td>
                      <td className="px-2 py-2">
                        <Badge variant={stateVariant[item.state]}>{stateLabel[item.state]}</Badge>
                      </td>
                      <td className="whitespace-nowrap px-2 py-2 text-muted-foreground">{formatAdminDate(item.created_at)}</td>
                      <td className="px-2 py-2">
                        {item.state === "withdrawn" ? (
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={busy === item.id}
                            onClick={() => void restore(item)}
                          >
                            <ArchiveRestore className="size-4" aria-hidden="true" />
                            {busy === item.id ? "恢复中…" : "恢复原版本"}
                          </Button>
                        ) : (
                          <span className="text-ui-xs text-muted-foreground">不可恢复</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>同步记录</CardTitle>
            <CardDescription>最近 20 次同步；保留 30 天。</CardDescription>
          </CardHeader>
          <CardContent>
            {runs.length === 0 ? (
              <EmptyState title="还没有同步记录" description="自动同步开启后，每个夜间窗口都会留下一条记录。" />
            ) : (
              <ul className="space-y-3">
                {runs.map((run) => (
                  <li key={run.id} className="min-w-0 border-b border-border/60 pb-3 last:border-0 last:pb-0">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="min-w-0 truncate text-ui-sm font-medium" title={run.source_name || ""}>
                        {run.source_name || "已删除的共享源"}
                      </p>
                      <div className="flex items-center gap-2">
                        <Badge variant={runStatusVariant[run.status]}>{runStatusLabel[run.status]}</Badge>
                        <span className="text-ui-xs text-muted-foreground">{triggerLabel[run.trigger]}</span>
                      </div>
                    </div>
                    <p className="mt-1 text-ui-xs text-muted-foreground">
                      {formatAdminDate(run.started_at)} · {runDuration(run)}
                    </p>
                    <p className="mt-1 break-words text-ui-xs text-foreground">{run.error_code || runSummary(run)}</p>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>最近事件</CardTitle>
            <CardDescription>每个文件的观测、导入、发布、撤回与清理动作。</CardDescription>
          </CardHeader>
          <CardContent>
            {events.length === 0 ? (
              <EmptyState title="还没有同步事件" description="同步运行后，逐文件动作会记录在这里。" />
            ) : (
              <ul className="space-y-3">
                {events.map((event) => (
                  <li key={event.id} className="min-w-0 border-b border-border/60 pb-3 last:border-0 last:pb-0">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="min-w-0 truncate text-ui-sm" title={event.relative_path || ""}>
                        {event.relative_path || "（未知文件）"}
                      </p>
                      <Badge variant={actionVariant[event.action]}>{actionLabel[event.action]}</Badge>
                    </div>
                    <p className="mt-1 break-words text-ui-xs text-muted-foreground">
                      {formatAdminDate(event.created_at)}
                      {event.error_code ? ` · ${event.error_code}` : ""}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
