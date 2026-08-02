import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { Card, CardContent } from "../../components/ui/card";
import type { AdminStats } from "../../types";
export function AdminOverviewPage() {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sweepResult, setSweepResult] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setStats(await api.adminStats());
      } catch (e: any) {
        setError(e?.message || String(e));
      }
    })();
  }, []);

  async function sweep() {
    if (!confirm("立即清理过期对话（>30 天未活动）和失效登录会话？")) return;
    try {
      const r = await api.adminSweep();
      setSweepResult(`已删除 ${r.deleted_conversations} 条对话、${r.deleted_auth_sessions} 条登录会话。`);
      setStats(await api.adminStats());
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }

  if (error) return <div className="text-sm text-red-600">{error}</div>;
  if (!stats) return <div className="text-sm text-muted">加载中…</div>;

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
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        {cards.map(([label, value, hint]) => (
          <Card key={label} className="shadow-surface">
            <CardContent className="p-4 pt-4">
              <div className="text-ui-xs font-medium text-muted-foreground">{label}</div>
              <div className="mt-1 text-ui-2xl font-semibold text-card-foreground">{value}</div>
              {hint && <div className="mt-1 text-ui-xs text-muted-foreground">{hint}</div>}
            </CardContent>
          </Card>
        ))}
      </div>
      <div className="rounded-lg border border-gray-200 bg-panel p-4 text-sm">
        <div className="font-medium mb-2">维护</div>
        <button
          type="button"
          onClick={sweep}
          className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50"
        >
          立即清理过期对话
        </button>
        {sweepResult && <div className="text-green-700 mt-2">{sweepResult}</div>}
        <p className="text-xs text-muted mt-2">
          清理策略：对话 30 天无活动即删除；失效登录会话同时清理。后台每小时自动执行一次。
        </p>
      </div>
    </div>
  );
}
