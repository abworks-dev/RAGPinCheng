import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { Card, CardContent } from "../../components/ui/card";
import { ErrorState } from "../../components/ui/error-state";
import { LoadingState } from "../../components/ui/loading-state";
import type { AdminStats } from "../../types";

const pageHeading = (
  <div>
    <p className="text-ui-sm font-medium text-primary">管理概览</p>
    <h1 className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">运营数据概览</h1>
    <p className="mt-2 max-w-2xl text-ui-sm text-muted-foreground">
      查看用户、对话和消息的整体情况。
    </p>
  </div>
);

export function AdminOverviewPage() {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setStats(await api.adminStats());
      } catch (e: any) {
        setError(e?.message || String(e));
      }
    })();
  }, []);

  if (error) {
    return (
      <div className="space-y-6">
        {pageHeading}
        <ErrorState title="概览加载失败" description={error} className="bg-card" />
      </div>
    );
  }

  if (!stats) {
    return (
      <div className="space-y-6">
        {pageHeading}
        <Card className="shadow-surface">
          <LoadingState className="min-h-48" label="正在加载管理概览…" />
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
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {cards.map(([label, value, hint]) => (
            <Card key={label} className="overflow-hidden shadow-surface">
              <CardContent className="relative p-5 pt-5">
                <span className="absolute inset-x-0 top-0 h-1 bg-primary/80" aria-hidden="true" />
                <div className="text-ui-xs font-medium text-muted-foreground">{label}</div>
                <div className="mt-2 text-ui-2xl font-semibold tabular-nums text-card-foreground">{value}</div>
                {hint && <div className="mt-2 text-ui-xs text-muted-foreground">{hint}</div>}
              </CardContent>
            </Card>
          ))}
        </div>
      </section>
    </div>
  );
}
