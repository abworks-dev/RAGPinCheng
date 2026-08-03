import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { LoadingState } from "../../components/ui/loading-state";
import type { AdminFeedbackEntry } from "../../types";

const kindLabels: Record<string, string> = {
  answer: "回答反馈",
  citation: "来源反馈",
};

function formatFeedbackDate(value?: string | null) {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function ratingBadge(rating?: string | null) {
  if (!rating) return null;
  if (rating === "up") return <Badge variant="success">有帮助</Badge>;
  if (rating === "down") return <Badge variant="destructive">需改进</Badge>;
  return <Badge variant="secondary">{rating}</Badge>;
}

const pageHeading = (
  <div>
    <p className="text-ui-sm font-medium text-primary">反馈管理</p>
    <h1 className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">问答与来源反馈</h1>
    <p className="mt-2 max-w-2xl text-ui-sm text-muted-foreground">
      查看用户对回答和引用来源的评价，辅助定位知识内容与回答质量问题。
    </p>
  </div>
);

export function AdminFeedbackPage() {
  const [entries, setEntries] = useState<AdminFeedbackEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await api.adminFeedback(200);
      setEntries(response.entries);
      setTotal(response.total);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const summary = useMemo(
    () => ({
      positive: entries.filter((entry) => entry.rating === "up").length,
      negative: entries.filter((entry) => entry.rating === "down").length,
      citation: entries.filter((entry) => entry.kind === "citation").length,
    }),
    [entries],
  );

  if (loading) {
    return (
      <div className="space-y-6">
        {pageHeading}
        <Card className="shadow-surface">
          <LoadingState className="min-h-48" label="正在加载反馈记录…" />
        </Card>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-6">
        {pageHeading}
        <ErrorState
          title="反馈记录加载失败"
          description={error}
          className="bg-card"
          action={
            <Button variant="outline" size="sm" onClick={refresh}>
              重新加载
            </Button>
          }
        />
      </div>
    );
  }

  if (entries.length === 0) {
    return (
      <div className="space-y-6">
        {pageHeading}
        <EmptyState title="暂无反馈记录" description="用户提交回答或来源反馈后，将在这里显示。" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {pageHeading}

      <section aria-label="反馈概览" className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card className="shadow-surface">
          <CardContent className="p-4 pt-4">
            <p className="text-ui-xs font-medium text-muted-foreground">有帮助</p>
            <p className="mt-2 text-ui-xl font-semibold tabular-nums text-success">{summary.positive}</p>
          </CardContent>
        </Card>
        <Card className="shadow-surface">
          <CardContent className="p-4 pt-4">
            <p className="text-ui-xs font-medium text-muted-foreground">需改进</p>
            <p className="mt-2 text-ui-xl font-semibold tabular-nums text-destructive">{summary.negative}</p>
          </CardContent>
        </Card>
        <Card className="shadow-surface">
          <CardContent className="p-4 pt-4">
            <p className="text-ui-xs font-medium text-muted-foreground">来源反馈</p>
            <p className="mt-2 text-ui-xl font-semibold tabular-nums text-info">{summary.citation}</p>
          </CardContent>
        </Card>
      </section>

      <section aria-labelledby="feedback-list-heading" className="space-y-3">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 id="feedback-list-heading" className="text-ui-base font-semibold text-foreground">
              最近反馈
            </h2>
            <p className="mt-1 text-ui-xs text-muted-foreground">
              共 {total} 条，当前显示最近 {entries.length} 条。
            </p>
          </div>
          <span className="text-ui-xs text-muted-foreground">按提交时间倒序</span>
        </div>

        <div className="space-y-4">
          {entries.map((entry, index) => (
            <Card
              key={`${entry.message_id || entry.parent_id || entry.ts || "feedback"}-${index}`}
              className="overflow-hidden shadow-surface"
            >
              <CardContent className="space-y-4 p-4 pt-4 sm:p-5 sm:pt-5">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline">{kindLabels[entry.kind || ""] || entry.kind || "未知类型"}</Badge>
                    {ratingBadge(entry.rating)}
                    {entry.category && <Badge variant="secondary">{entry.category}</Badge>}
                  </div>
                  <time className="text-ui-xs text-muted-foreground" dateTime={entry.ts || undefined}>
                    {formatFeedbackDate(entry.ts)}
                  </time>
                </div>

                {entry.query && (
                  <div className="rounded-ui-xl border border-border bg-surface-muted/60 p-3">
                    <p className="text-ui-xs font-medium text-muted-foreground">用户问题</p>
                    <p className="mt-1 whitespace-pre-wrap text-ui-sm leading-relaxed text-foreground">{entry.query}</p>
                  </div>
                )}

                {entry.note && (
                  <div className="rounded-ui-xl border border-primary/20 bg-primary/5 p-3">
                    <p className="text-ui-xs font-medium text-primary">用户补充说明</p>
                    <p className="mt-1 whitespace-pre-wrap text-ui-sm leading-relaxed text-foreground">{entry.note}</p>
                  </div>
                )}

                {entry.doc_title && (
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-ui-xs text-muted-foreground">
                    <span className="font-medium text-foreground">来源</span>
                    <span>[{entry.doc_title}]</span>
                    {entry.section_path && <span>{entry.section_path}</span>}
                    {entry.start_time && <span>@{entry.start_time}</span>}
                  </div>
                )}

                {entry.answer_text && (
                  <details className="rounded-ui-xl border border-border bg-card">
                    <summary className="cursor-pointer px-3 py-2 text-ui-xs font-medium text-foreground hover:bg-surface-muted/60">
                      查看关联回答
                    </summary>
                    <div className="border-t border-border px-3 py-3 whitespace-pre-wrap text-ui-sm leading-relaxed text-muted-foreground">
                      {entry.answer_text}
                    </div>
                  </details>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      </section>
    </div>
  );
}
