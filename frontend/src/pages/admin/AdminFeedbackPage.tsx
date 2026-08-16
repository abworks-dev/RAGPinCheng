import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { LoadingState } from "../../components/ui/loading-state";
import type { AdminFeedbackEntry, AdminFeedbackResponse } from "../../types";

type FeedbackStatus = AdminFeedbackEntry["status"];
type Resolution = NonNullable<AdminFeedbackEntry["resolution"]>;

const statusLabels: Record<FeedbackStatus | "all", string> = {
  pending: "待处理",
  in_progress: "处理中",
  resolved: "已完成",
  archived: "已归档",
  all: "全部",
};
const kindLabels: Record<string, string> = { answer: "回答反馈", citation: "来源反馈" };
const resolutionLabels: Record<Resolution, string> = {
  knowledge_fixed: "已修复知识内容",
  answer_improved: "已优化回答",
  no_action: "无需处理",
  duplicate: "重复反馈",
  other: "其他",
};
const emptyCounts = { pending: 0, in_progress: 0, resolved: 0, archived: 0 };
const pageSize = 20;

function formatDate(value?: string | null) {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function ratingBadge(rating?: string | null) {
  if (rating === "up") return <Badge variant="success">有帮助</Badge>;
  if (rating === "down") return <Badge variant="destructive">需改进</Badge>;
  return rating ? <Badge variant="secondary">{rating}</Badge> : null;
}

function statusBadge(status: FeedbackStatus) {
  const variant = status === "resolved" ? "success" : status === "pending" ? "destructive" : "secondary";
  return <Badge variant={variant}>{statusLabels[status]}</Badge>;
}

const heading = (
  <div>
    <p className="text-ui-xs font-medium text-primary">运营管理</p>
    <h1 className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">用户反馈</h1>
    <p className="mt-2 max-w-2xl text-ui-sm text-muted-foreground">
      跟进用户对回答和引用来源的评价，记录处理结果并形成质量改进闭环。
    </p>
  </div>
);

export function AdminFeedbackPage() {
  const [response, setResponse] = useState<AdminFeedbackResponse>({
    entries: [], total: 0, page: 1, page_size: pageSize, counts: emptyCounts,
  });
  const [status, setStatus] = useState<FeedbackStatus | "all">("pending");
  const [kind, setKind] = useState("");
  const [rating, setRating] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [resolution, setResolution] = useState<Resolution>("knowledge_fixed");
  const [adminNote, setAdminNote] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setResponse(await api.adminFeedback({ status, kind, rating, q: query, page, page_size: pageSize }));
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [status, kind, rating, query, page]);

  useEffect(() => { void refresh(); }, [refresh]);

  const patch = async (
    entry: AdminFeedbackEntry,
    nextStatus: FeedbackStatus,
    extra: { resolution?: Resolution; admin_note?: string } = {},
  ) => {
    setBusyId(entry.feedback_id);
    setError(null);
    try {
      await api.adminPatchFeedback(entry.feedback_id, { status: nextStatus, ...extra });
      setEditingId(null);
      await refresh();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusyId(null);
    }
  };

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setPage(1);
    setQuery(searchInput.trim());
  };

  const totalPages = Math.max(1, Math.ceil(response.total / response.page_size));

  return (
    <div className="space-y-6">
      {heading}

      <section aria-label="反馈概览" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {(["pending", "in_progress", "resolved", "archived"] as FeedbackStatus[]).map((item) => (
          <button key={item} type="button" className="text-left" onClick={() => { setStatus(item); setPage(1); }}>
            <Card className={status === item ? "border-primary shadow-surface" : "shadow-surface"}>
              <CardContent className="p-4 pt-4">
                <p className="text-ui-xs font-medium text-muted-foreground">{statusLabels[item]}</p>
                <p className="mt-2 text-ui-xl font-semibold tabular-nums text-foreground">{response.counts[item]}</p>
              </CardContent>
            </Card>
          </button>
        ))}
      </section>

      <Card className="shadow-surface">
        <CardContent className="flex flex-col gap-3 p-4 pt-4 lg:flex-row">
          <form className="flex min-w-0 flex-1 gap-2" onSubmit={submitSearch}>
            <input
              aria-label="搜索反馈"
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="搜索问题、说明、回答或文档…"
              className="min-w-0 flex-1 rounded-ui-lg border border-input bg-background px-3 py-2 text-ui-sm"
            />
            <Button type="submit" variant="outline">搜索</Button>
          </form>
          <select aria-label="处理状态" value={status} onChange={(e) => { setStatus(e.target.value as FeedbackStatus | "all"); setPage(1); }} className="rounded-ui-lg border border-input bg-background px-3 py-2 text-ui-sm">
            {Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <select aria-label="反馈类型" value={kind} onChange={(e) => { setKind(e.target.value); setPage(1); }} className="rounded-ui-lg border border-input bg-background px-3 py-2 text-ui-sm">
            <option value="">全部类型</option><option value="answer">回答反馈</option><option value="citation">来源反馈</option>
          </select>
          <select aria-label="用户评价" value={rating} onChange={(e) => { setRating(e.target.value); setPage(1); }} className="rounded-ui-lg border border-input bg-background px-3 py-2 text-ui-sm">
            <option value="">全部评价</option><option value="up">有帮助</option><option value="down">需改进</option>
          </select>
        </CardContent>
      </Card>

      {error && <ErrorState title="反馈操作失败" description={error} className="bg-card" action={<Button variant="outline" size="sm" onClick={refresh}>重新加载</Button>} />}
      {loading ? (
        <Card className="shadow-surface"><LoadingState className="min-h-48" label="正在加载反馈记录…" /></Card>
      ) : response.entries.length === 0 ? (
        <EmptyState
          title={status === "pending" ? "所有反馈均已处理" : "没有符合条件的反馈"}
          description={status === "pending" ? "当前没有待处理反馈，可以查看已完成或全部记录。" : "请尝试调整状态、类型、评价或搜索条件。"}
        />
      ) : (
        <section aria-labelledby="feedback-list-heading" className="space-y-3">
          <div className="flex items-end justify-between">
            <div>
              <h2 id="feedback-list-heading" className="text-ui-base font-semibold">反馈队列</h2>
              <p className="mt-1 text-ui-xs text-muted-foreground">筛选结果 {response.total} 条，第 {response.page}/{totalPages} 页。</p>
            </div>
            <span className="text-ui-xs text-muted-foreground">按提交时间倒序</span>
          </div>
          <div className="space-y-4">
            {response.entries.map((entry) => (
              <Card key={entry.feedback_id} className="overflow-hidden shadow-surface">
                <CardContent className="space-y-4 p-4 pt-4 sm:p-5 sm:pt-5">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="outline">{kindLabels[entry.kind || ""] || "未知类型"}</Badge>
                      {ratingBadge(entry.rating)}
                      {statusBadge(entry.status)}
                      {entry.category && <Badge variant="secondary">{entry.category}</Badge>}
                    </div>
                    <div className="flex flex-wrap items-center justify-end gap-2">
                      <time className="mr-1 text-ui-xs text-muted-foreground" dateTime={entry.ts || undefined}>{formatDate(entry.ts)}</time>
                      {entry.status === "pending" && <Button size="sm" onClick={() => void patch(entry, "in_progress")} disabled={busyId === entry.feedback_id}>开始处理</Button>}
                      {entry.status !== "resolved" && entry.status !== "archived" && <Button size="sm" variant="outline" onClick={() => { setEditingId(entry.feedback_id); setAdminNote(entry.admin_note || ""); }}>标记完成</Button>}
                      {entry.status === "resolved" && <Button size="sm" variant="outline" onClick={() => void patch(entry, "pending", { admin_note: entry.admin_note || "" })}>重新打开</Button>}
                      {entry.status !== "archived" && <Button size="sm" variant="ghost" onClick={() => void patch(entry, "archived", { admin_note: entry.admin_note || "" })}>归档</Button>}
                      {entry.status === "archived" && <Button size="sm" variant="outline" onClick={() => void patch(entry, "pending", { admin_note: entry.admin_note || "" })}>恢复</Button>}
                    </div>
                  </div>
                  {entry.query && <div className="rounded-ui-xl border border-border bg-surface-muted/60 p-3"><p className="text-ui-xs font-medium text-muted-foreground">用户问题</p><p className="mt-1 whitespace-pre-wrap text-ui-sm leading-relaxed">{entry.query}</p></div>}
                  {entry.note && <div className="rounded-ui-xl border border-primary/20 bg-primary/5 p-3"><p className="text-ui-xs font-medium text-primary">用户补充说明</p><p className="mt-1 whitespace-pre-wrap text-ui-sm leading-relaxed">{entry.note}</p></div>}
                  {entry.doc_title && <div className="flex flex-wrap gap-2 text-ui-xs text-muted-foreground"><span className="font-medium text-foreground">来源</span><span>[{entry.doc_title}]</span>{entry.section_path && <span>{entry.section_path}</span>}{entry.start_time && <span>@{entry.start_time}</span>}</div>}
                  {entry.resolution && <div className="rounded-ui-xl border border-success/20 bg-success/5 p-3 text-ui-sm"><span className="font-medium text-success">处理结果：</span>{resolutionLabels[entry.resolution]}{entry.assignee_name && <span className="ml-2 text-muted-foreground">由 {entry.assignee_name} 处理</span>}{entry.admin_note && <p className="mt-1 text-muted-foreground">{entry.admin_note}</p>}</div>}
                  {editingId === entry.feedback_id && <div className="space-y-3 rounded-ui-xl border border-primary/30 bg-primary/5 p-3">
                    <p className="text-ui-sm font-medium">完成处理</p>
                    <select aria-label="处理结果" value={resolution} onChange={(e) => setResolution(e.target.value as Resolution)} className="w-full rounded-ui-lg border border-input bg-background px-3 py-2 text-ui-sm">
                      {Object.entries(resolutionLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </select>
                    <textarea aria-label="处理备注" value={adminNote} onChange={(e) => setAdminNote(e.target.value)} maxLength={2000} placeholder="可选：记录具体修改或判断依据" className="min-h-20 w-full rounded-ui-lg border border-input bg-background px-3 py-2 text-ui-sm" />
                    <div className="flex justify-end gap-2"><Button variant="ghost" size="sm" onClick={() => setEditingId(null)}>取消</Button><Button size="sm" onClick={() => void patch(entry, "resolved", { resolution, admin_note: adminNote.trim() })} disabled={busyId === entry.feedback_id}>确认完成</Button></div>
                  </div>}
                  {entry.answer_text && <details className="rounded-ui-xl border border-border bg-card"><summary className="cursor-pointer px-3 py-2 text-ui-xs font-medium hover:bg-surface-muted/60">查看关联回答</summary><div className="border-t border-border px-3 py-3 whitespace-pre-wrap text-ui-sm leading-relaxed text-muted-foreground">{entry.answer_text}</div></details>}
                </CardContent>
              </Card>
            ))}
          </div>
          <div className="flex items-center justify-end gap-2">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</Button>
            <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</Button>
          </div>
        </section>
      )}
    </div>
  );
}
