import { useEffect, useState } from "react";
import { ArrowRight } from "lucide-react";
import { adminOverviewApi } from "../../api/admin/overview";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { ErrorState } from "../../components/ui/error-state";
import { LoadingState } from "../../components/ui/loading-state";
import type { AdminStats, MaintenanceStatus, SystemOverview } from "../../types";
import { formatAdminDate } from "../../lib/admin-formatters";
import { ProductionRuntimeStatus } from "./ProductionRuntimeStatus";

const pageHeading = (
  <div>
    <p className="text-ui-xs font-medium text-primary">总览</p>
    <h1 className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">系统概览</h1>
    <p className="mt-2 max-w-2xl text-ui-sm text-muted-foreground">
      查看用户、对话和消息的整体情况。
    </p>
  </div>
);

type AdminOverviewPageProps = {
  onOpenMaintenance?: () => void;
};

function retentionLabel(days: number | null) {
  return days === null ? "永久保留" : `保留 ${days} 天`;
}

export function AdminOverviewPage({ onOpenMaintenance }: AdminOverviewPageProps) {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [maintenance, setMaintenance] = useState<MaintenanceStatus | null>(null);
  const [maintenanceError, setMaintenanceError] = useState<string | null>(null);
  const [runtime, setRuntime] = useState<SystemOverview | null>(null);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);
  const [runtimeLoading, setRuntimeLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        setStats(await adminOverviewApi.stats());
      } catch (e: any) {
        setError(e?.message || String(e));
      }
    })();
    (async () => {
      try {
        setMaintenance(await adminOverviewApi.maintenance());
      } catch (e: any) {
        setMaintenanceError(e?.message || String(e));
      }
    })();
    void loadRuntime();
  }, []);

  async function loadRuntime() {
    setRuntimeLoading(true);
    setRuntimeError(null);
    try {
      setRuntime(await adminOverviewApi.systemOverview());
    } catch (e: any) {
      setRuntimeError(e?.message || String(e));
    } finally {
      setRuntimeLoading(false);
    }
  }

  if (error) {
    return (
      <div className="space-y-6">
        {pageHeading}
        <ErrorState title="系统概览加载失败" description={error} className="bg-card" />
      </div>
    );
  }

  if (!stats) {
    return (
      <div className="space-y-6">
        {pageHeading}
        <Card className="shadow-surface">
          <LoadingState className="min-h-48" label="正在加载系统概览…" />
        </Card>
      </div>
    );
  }

  const cards: [string, number, string][] = [
    ["用户总数", stats.users_total, "已注册账号"],
    ["启用用户", stats.users_active, "未停用账号"],
    ["对话总数", stats.conversations_total, "活跃保留中"],
    ["对话（近 7 天）", stats.conversations_7d, "最近 7 天有新消息"],
    ["消息总数", stats.messages_total, "用户 + 助手"],
    ["消息（近 7 天）", stats.messages_7d, ""],
  ];

  return (
    <div className="space-y-6">
      {pageHeading}

      <section aria-labelledby="overview-stats-heading" className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <h2 id="overview-stats-heading" className="text-ui-base font-semibold text-foreground">
            核心指标
          </h2>
          <span className="text-ui-xs text-muted-foreground">当前数据</span>
        </div>
        <dl className="grid grid-cols-1 overflow-hidden rounded-ui-xl border border-border bg-card divide-y divide-border sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-3 xl:divide-y">
          {cards.map(([label, value, hint]) => (
            <div key={label} className="min-w-0 p-4 sm:p-5">
              <dt className="text-ui-xs font-medium text-muted-foreground">{label}</dt>
              <dd className="mt-1 text-ui-xl font-semibold tabular-nums text-card-foreground">{value}</dd>
              {hint && <dd className="mt-1 text-ui-xs text-muted-foreground">{hint}</dd>}
            </div>
          ))}
        </dl>
      </section>

      <ProductionRuntimeStatus
        data={runtime}
        loading={runtimeLoading}
        error={runtimeError}
        onRefresh={() => void loadRuntime()}
      />

      <section aria-labelledby="overview-maintenance-heading" className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <h2 id="overview-maintenance-heading" className="text-ui-base font-semibold text-foreground">系统维护</h2>
          {onOpenMaintenance && <Button variant="ghost" size="sm" onClick={onOpenMaintenance}>查看系统维护<ArrowRight className="size-4" /></Button>}
        </div>
        <div className="overflow-hidden rounded-ui-xl border border-border">
          {maintenanceError ? (
            <div className="flex flex-col gap-2 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-ui-sm text-destructive">维护状态暂不可用</p>
              {onOpenMaintenance && <Button variant="outline" size="sm" onClick={onOpenMaintenance}>查看详情</Button>}
            </div>
          ) : !maintenance ? (
            <LoadingState className="min-h-24" label="正在加载维护状态…" />
          ) : (
            <dl className="grid grid-cols-1 gap-px bg-border sm:grid-cols-2 xl:grid-cols-4">
              <div className="bg-card p-4"><dt className="text-ui-xs text-muted-foreground">自动清理</dt><dd className="mt-2"><Badge variant={maintenance.settings.conversation_cleanup_enabled ? "success" : "warning"}>{maintenance.settings.conversation_cleanup_enabled ? "已启用" : "已停用"}</Badge></dd></div>
              <div className="bg-card p-4"><dt className="text-ui-xs text-muted-foreground">当前策略</dt><dd className="mt-2 text-ui-sm font-medium">{retentionLabel(maintenance.settings.conversation_retention_days)}</dd></div>
              <div className="bg-card p-4"><dt className="text-ui-xs text-muted-foreground">最近运行</dt><dd className="mt-2"><Badge variant={!maintenance.last_run ? "secondary" : maintenance.last_run.status === "succeeded" ? "success" : "destructive"}>{!maintenance.last_run ? "尚无记录" : maintenance.last_run.status === "succeeded" ? "成功" : "失败"}</Badge></dd></div>
              <div className="bg-card p-4"><dt className="text-ui-xs text-muted-foreground">运行时间</dt><dd className="mt-2 text-ui-sm font-medium">{maintenance.last_run ? formatAdminDate(maintenance.last_run.finished_at) : "—"}</dd></div>
            </dl>
          )}
        </div>
      </section>
    </div>
  );
}
