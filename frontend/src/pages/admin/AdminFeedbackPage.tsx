import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  Archive,
  ArchiveRestore,
  Check,
  MessageSquareText,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
} from "lucide-react";
import { adminConversationsApi } from "../../api/admin/conversations";
import { adminFeedbackApi } from "../../api/admin/feedback";
import { AdminConversationDetail } from "../../components/admin/AdminConversationDetail";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent } from "../../components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { IconButton } from "../../components/ui/icon-button";
import { Input } from "../../components/ui/input";
import { LoadingState } from "../../components/ui/loading-state";
import { Select } from "../../components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../../components/ui/sheet";
import { toast } from "../../components/ui/toast";
import { cn } from "../../lib/utils";
import type { AdminFeedbackEntry, AdminFeedbackResponse, ConversationState } from "../../types";

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

function formatDate(value?: string | number | null) {
  if (!value) return "时间未知";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function dateTimeValue(value?: string | number | null) {
  if (!value) return undefined;
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

function feedbackTitle(entry: AdminFeedbackEntry) {
  const query = entry.query?.trim();
  if (query) return query;
  if (entry.doc_title) return `关于《${entry.doc_title}》的来源反馈`;
  const note = entry.note?.trim().split("\n")[0];
  return note || kindLabels[entry.kind || ""] || "未命名反馈";
}

function feedbackSummary(entry: AdminFeedbackEntry) {
  if (entry.note?.trim()) return entry.note.trim();
  if (entry.section_path) return entry.section_path;
  if (entry.answer_text?.trim()) return entry.answer_text.trim();
  return "用户未提供补充说明";
}

function ratingBadge(rating?: string | null) {
  if (rating === "up") return <Badge variant="success">有帮助</Badge>;
  if (rating === "down") return <Badge variant="destructive">需改进</Badge>;
  return rating ? <Badge variant="secondary">{rating}</Badge> : null;
}

function statusBadge(status: FeedbackStatus) {
  const variant = status === "resolved" ? "success"
    : status === "pending" ? "destructive"
    : status === "in_progress" ? "info"
    : "secondary";
  return <Badge variant={variant}>{statusLabels[status]}</Badge>;
}

function FeedbackSummaryCard({
  status,
  count,
  active,
  onSelect,
}: {
  status: FeedbackStatus;
  count: number;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className="w-full text-left"
      aria-pressed={active}
      onClick={onSelect}
    >
      <Card className={cn("h-full overflow-hidden shadow-surface transition-colors", active && "border-primary")}>
        <CardContent className="relative p-4 pt-4">
          <span className={cn("absolute inset-x-0 top-0 h-1", active ? "bg-primary" : "bg-border")} aria-hidden="true" />
          <p className="text-ui-xs font-medium text-muted-foreground">{statusLabels[status]}</p>
          <p className="mt-2 text-ui-xl font-semibold tabular-nums text-foreground">{count}</p>
        </CardContent>
      </Card>
    </button>
  );
}

function FeedbackDetail({
  entry,
  busy,
  onStart,
  onComplete,
  onArchive,
  onRestore,
  onReopen,
  onViewConversation,
}: {
  entry: AdminFeedbackEntry;
  busy: boolean;
  onStart: () => void;
  onComplete: () => void;
  onArchive: () => void;
  onRestore: () => void;
  onReopen: () => void;
  onViewConversation: () => void;
}) {
  return (
    <article aria-labelledby={`feedback-detail-${entry.feedback_id}`} className="min-w-0">
      <header className="flex flex-col gap-4 border-b border-border px-4 py-4 sm:px-5 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{kindLabels[entry.kind || ""] || "未知类型"}</Badge>
            {ratingBadge(entry.rating)}
            {statusBadge(entry.status)}
            {entry.category && <Badge variant="secondary">{entry.category}</Badge>}
          </div>
          <h3 id={`feedback-detail-${entry.feedback_id}`} className="mt-3 break-words text-ui-lg font-semibold text-foreground">
            {feedbackTitle(entry)}
          </h3>
          <time className="mt-1 block text-ui-xs text-muted-foreground" dateTime={dateTimeValue(entry.ts)}>
            提交于 {formatDate(entry.ts)}
          </time>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {entry.status === "pending" && (
            <Button size="sm" disabled={busy} onClick={onStart}>
              <Play className="size-4" />{busy ? "处理中…" : "开始处理"}
            </Button>
          )}
          {entry.status !== "resolved" && entry.status !== "archived" && (
            <Button size="sm" variant={entry.status === "in_progress" ? "default" : "outline"} disabled={busy} onClick={onComplete}>
              <Check className="size-4" />标记完成
            </Button>
          )}
          {entry.status === "resolved" && (
            <Button size="sm" variant="outline" disabled={busy} onClick={onReopen}>
              <RotateCcw className="size-4" />重新打开
            </Button>
          )}
          {entry.status !== "archived" ? (
            <IconButton
              label="归档反馈"
              tooltip="归档这条反馈，可在已归档中恢复"
              className="border border-border"
              disabled={busy}
              onClick={onArchive}
            >
              <Archive className="size-4" />
            </IconButton>
          ) : (
            <Button size="sm" variant="outline" disabled={busy} onClick={onRestore}>
              <ArchiveRestore className="size-4" />恢复
            </Button>
          )}
        </div>
      </header>

      <div className="divide-y divide-border px-4 sm:px-5">
        {entry.note && (
          <section className="py-4" aria-labelledby={`feedback-note-${entry.feedback_id}`}>
            <h4 id={`feedback-note-${entry.feedback_id}`} className="text-ui-sm font-semibold text-foreground">用户反馈</h4>
            <p className="mt-2 whitespace-pre-wrap break-words border-l-2 border-primary bg-primary/5 px-3 py-2 text-ui-sm leading-relaxed">
              {entry.note}
            </p>
          </section>
        )}

        {(entry.query || entry.answer_text) && (
          <section className="py-4" aria-labelledby={`feedback-context-${entry.feedback_id}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h4 id={`feedback-context-${entry.feedback_id}`} className="text-ui-sm font-semibold text-foreground">关联问答</h4>
              {entry.conversation_id && (
                <Button size="sm" variant="ghost" onClick={onViewConversation}>
                  <MessageSquareText className="size-4" />查看完整对话
                </Button>
              )}
            </div>
            {entry.query && (
              <div className="mt-2 bg-surface-muted px-3 py-2">
                <p className="text-ui-xs font-medium text-muted-foreground">用户问题</p>
                <p className="mt-1 whitespace-pre-wrap break-words text-ui-sm leading-relaxed">{entry.query}</p>
              </div>
            )}
            {entry.answer_text && (
              <div className="mt-2 max-h-64 overflow-y-auto border border-border px-3 py-2">
                <p className="text-ui-xs font-medium text-muted-foreground">助手回答</p>
                <p className="mt-1 whitespace-pre-wrap break-words text-ui-sm leading-relaxed text-foreground">{entry.answer_text}</p>
              </div>
            )}
          </section>
        )}

        {entry.doc_title && (
          <section className="py-4" aria-labelledby={`feedback-source-${entry.feedback_id}`}>
            <h4 id={`feedback-source-${entry.feedback_id}`} className="text-ui-sm font-semibold text-foreground">关联来源</h4>
            <dl className="mt-2 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-2 text-ui-sm">
              <dt className="text-muted-foreground">资料</dt><dd className="break-words">《{entry.doc_title}》</dd>
              {entry.section_path && <><dt className="text-muted-foreground">位置</dt><dd className="break-words">{entry.section_path}</dd></>}
              {entry.start_time && <><dt className="text-muted-foreground">时间</dt><dd>{entry.start_time}</dd></>}
            </dl>
          </section>
        )}

        <section className="py-4" aria-labelledby={`feedback-workflow-${entry.feedback_id}`}>
          <h4 id={`feedback-workflow-${entry.feedback_id}`} className="text-ui-sm font-semibold text-foreground">处理信息</h4>
          <dl className="mt-3 grid gap-3 sm:grid-cols-3">
            <div><dt className="text-ui-xs text-muted-foreground">当前状态</dt><dd className="mt-1 text-ui-sm font-medium">{statusLabels[entry.status]}</dd></div>
            <div><dt className="text-ui-xs text-muted-foreground">负责人</dt><dd className="mt-1 text-ui-sm font-medium">{entry.assignee_name || "未领取"}</dd></div>
            <div><dt className="text-ui-xs text-muted-foreground">最后更新</dt><dd className="mt-1 text-ui-sm font-medium">{formatDate(entry.updated_at || entry.ts)}</dd></div>
          </dl>
          {entry.resolution && (
            <div className="mt-4 border-l-2 border-success bg-success/5 px-3 py-2 text-ui-sm">
              <p><span className="font-medium text-success">处理结果：</span>{resolutionLabels[entry.resolution]}</p>
              {entry.admin_note && <p className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">{entry.admin_note}</p>}
            </div>
          )}
        </section>
      </div>
    </article>
  );
}

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
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  const [completionTarget, setCompletionTarget] = useState<AdminFeedbackEntry | null>(null);
  const [resolution, setResolution] = useState<Resolution>("knowledge_fixed");
  const [adminNote, setAdminNote] = useState("");
  const [conversationOpen, setConversationOpen] = useState(false);
  const [conversation, setConversation] = useState<ConversationState | null>(null);
  const [conversationLoading, setConversationLoading] = useState(false);
  const [conversationError, setConversationError] = useState<string | null>(null);

  const refresh = useCallback(async (background = false) => {
    background ? setRefreshing(true) : setLoading(true);
    setError(null);
    try {
      setResponse(await adminFeedbackApi.list({ status, kind, rating, q: query, page, page_size: pageSize }));
    } catch (caught: any) {
      setError(caught?.message || String(caught));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [status, kind, rating, query, page]);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    if (response.entries.length === 0) {
      setSelectedId(null);
      setMobileDetailOpen(false);
      return;
    }
    setSelectedId((current) => response.entries.some((entry) => entry.feedback_id === current)
      ? current
      : response.entries[0].feedback_id);
  }, [response.entries]);

  const selectedEntry = useMemo(
    () => response.entries.find((entry) => entry.feedback_id === selectedId) || response.entries[0] || null,
    [response.entries, selectedId],
  );

  const patch = async (
    entry: AdminFeedbackEntry,
    nextStatus: FeedbackStatus,
    extra: { resolution?: Resolution; admin_note?: string } = {},
  ) => {
    setBusyId(entry.feedback_id);
    setError(null);
    try {
      await adminFeedbackApi.patch(entry.feedback_id, { status: nextStatus, ...extra });
      const successMessage = nextStatus === "in_progress" ? "已领取反馈"
        : nextStatus === "resolved" ? "反馈已完成"
        : nextStatus === "archived" ? "反馈已归档"
        : entry.status === "archived" ? "反馈已恢复"
        : "反馈已重新打开";
      toast.success(successMessage);
      setCompletionTarget(null);
      await refresh(true);
    } catch (caught: any) {
      const message = caught?.message || String(caught);
      setError(message);
      toast.error(message || "反馈操作失败");
    } finally {
      setBusyId(null);
    }
  };

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setPage(1);
    setQuery(searchInput.trim());
  };

  const selectStatus = (nextStatus: FeedbackStatus | "all") => {
    setStatus(nextStatus);
    setPage(1);
    setMobileDetailOpen(false);
  };

  const clearSearchFilters = () => {
    setSearchInput("");
    setQuery("");
    setKind("");
    setRating("");
    setPage(1);
  };

  const selectEntry = (entry: AdminFeedbackEntry) => {
    setSelectedId(entry.feedback_id);
    if (window.matchMedia?.("(max-width: 1279px)").matches) setMobileDetailOpen(true);
  };

  const openCompletion = (entry: AdminFeedbackEntry) => {
    setCompletionTarget(entry);
    setResolution(entry.resolution || "knowledge_fixed");
    setAdminNote(entry.admin_note || "");
  };

  const openConversation = async (entry: AdminFeedbackEntry) => {
    if (!entry.conversation_id) return;
    setMobileDetailOpen(false);
    setConversationOpen(true);
    setConversation(null);
    setConversationError(null);
    setConversationLoading(true);
    try {
      setConversation(await adminConversationsApi.get(entry.conversation_id));
    } catch (caught: any) {
      setConversationError(caught?.message || String(caught));
    } finally {
      setConversationLoading(false);
    }
  };

  const detail = selectedEntry ? (
    <FeedbackDetail
      entry={selectedEntry}
      busy={busyId === selectedEntry.feedback_id}
      onStart={() => void patch(selectedEntry, "in_progress")}
      onComplete={() => openCompletion(selectedEntry)}
      onArchive={() => void patch(selectedEntry, "archived", { admin_note: selectedEntry.admin_note || "" })}
      onRestore={() => void patch(selectedEntry, "pending", { admin_note: selectedEntry.admin_note || "" })}
      onReopen={() => void patch(selectedEntry, "pending", { admin_note: selectedEntry.admin_note || "" })}
      onViewConversation={() => void openConversation(selectedEntry)}
    />
  ) : null;

  const totalPages = Math.max(1, Math.ceil(response.total / response.page_size));
  const hasSearchFilters = Boolean(query || searchInput || kind || rating);

  return (
    <section className="space-y-5" aria-labelledby="admin-feedback-title">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-ui-xs font-medium text-primary">运营管理</p>
          <h1 id="admin-feedback-title" className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">用户反馈</h1>
          <p className="mt-1 max-w-3xl text-ui-sm text-muted-foreground">
            集中分诊回答和引用问题，保留处理依据并形成质量改进闭环。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant={status === "all" ? "default" : "outline"} onClick={() => selectStatus("all")}>
            全部反馈
          </Button>
          <Button size="sm" variant="outline" disabled={loading || refreshing} onClick={() => void refresh(true)}>
            <RefreshCw className={cn("size-4", refreshing && "animate-spin")} />{refreshing ? "刷新中" : "刷新"}
          </Button>
        </div>
      </header>

      <section aria-label="反馈状态概览" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {(["pending", "in_progress", "resolved", "archived"] as FeedbackStatus[]).map((item) => (
          <FeedbackSummaryCard
            key={item}
            status={item}
            count={response.counts[item]}
            active={status === item}
            onSelect={() => selectStatus(item)}
          />
        ))}
      </section>

      <Card className="overflow-hidden shadow-surface" aria-labelledby="feedback-list-heading">
        <div className="flex flex-col gap-2 px-4 py-4 sm:flex-row sm:items-end sm:justify-between sm:px-5">
          <div>
            <h2 id="feedback-list-heading" className="text-ui-base font-semibold text-foreground">反馈队列</h2>
            <p className="mt-1 text-ui-xs text-muted-foreground" role="status" aria-live="polite">
              {statusLabels[status]} · 共 {response.total} 条 · 按提交时间倒序
            </p>
          </div>
          <p className="text-ui-xs tabular-nums text-muted-foreground">第 {response.page} / {totalPages} 页</p>
        </div>

        <div className="grid gap-3 border-t border-border px-4 py-4 md:grid-cols-2 xl:grid-cols-[minmax(16rem,1fr)_11rem_11rem_auto] xl:items-end sm:px-5">
          <form className="space-y-1 md:col-span-2 xl:col-span-1" onSubmit={submitSearch}>
            <label htmlFor="feedback-search" className="text-ui-xs text-muted-foreground">搜索</label>
            <div className="flex gap-2">
              <span className="relative min-w-0 flex-1">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
                <Input
                  id="feedback-search"
                  type="search"
                  value={searchInput}
                  onChange={(event) => setSearchInput(event.target.value)}
                  placeholder="问题、反馈说明、回答或资料…"
                  className="pl-9"
                />
              </span>
              <Button type="submit" variant="outline">搜索</Button>
            </div>
          </form>
          <label className="space-y-1 text-ui-xs text-muted-foreground">
            <span>反馈类型</span>
            <Select aria-label="反馈类型" value={kind} onChange={(event) => { setKind(event.target.value); setPage(1); }}>
              <option value="">全部类型</option><option value="answer">回答反馈</option><option value="citation">来源反馈</option>
            </Select>
          </label>
          <label className="space-y-1 text-ui-xs text-muted-foreground">
            <span>用户评价</span>
            <Select aria-label="用户评价" value={rating} onChange={(event) => { setRating(event.target.value); setPage(1); }}>
              <option value="">全部评价</option><option value="up">有帮助</option><option value="down">需改进</option>
            </Select>
          </label>
          <Button variant="outline" disabled={!hasSearchFilters} onClick={clearSearchFilters}>清除筛选</Button>
        </div>

        {error && response.entries.length > 0 && (
          <ErrorState
            className="rounded-none border-x-0 border-b-0"
            title="反馈操作失败"
            description={error}
            action={<Button variant="outline" size="sm" onClick={() => void refresh()}>重新加载</Button>}
          />
        )}

        {loading ? (
          <LoadingState className="min-h-64 rounded-none border-x-0 border-b-0" label="正在加载反馈记录…" />
        ) : error && response.entries.length === 0 ? (
          <ErrorState
            className="min-h-64 rounded-none border-x-0 border-b-0"
            title="反馈列表加载失败"
            description={error}
            action={<Button variant="outline" size="sm" onClick={() => void refresh()}>重新加载</Button>}
          />
        ) : response.entries.length === 0 ? (
          <EmptyState
            className="min-h-64 rounded-none border-x-0 border-b-0"
            title={status === "pending" && !hasSearchFilters ? "所有反馈均已处理" : "没有符合条件的反馈"}
            description={status === "pending" && !hasSearchFilters ? "当前没有待处理反馈，可以查看处理中、已完成或全部记录。" : "请调整状态、类型、评价或搜索条件。"}
            action={hasSearchFilters ? <Button variant="outline" size="sm" onClick={clearSearchFilters}>清除筛选</Button> : undefined}
          />
        ) : (
          <div className="border-t border-border xl:grid xl:min-h-[36rem] xl:grid-cols-[minmax(19rem,0.82fr)_minmax(0,1.35fr)]">
            <section aria-label="反馈列表" className="min-w-0 xl:border-r xl:border-border">
              <ol className="divide-y divide-border xl:max-h-[42rem] xl:overflow-y-auto">
                {response.entries.map((entry) => {
                  const selected = selectedEntry?.feedback_id === entry.feedback_id;
                  return (
                    <li key={entry.feedback_id}>
                      <button
                        type="button"
                        aria-pressed={selected}
                        onClick={() => selectEntry(entry)}
                        className={cn(
                          "w-full border-l-2 px-4 py-4 text-left transition-colors focus-visible:relative focus-visible:z-base focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring sm:px-5",
                          selected ? "border-l-primary bg-primary/10" : "border-l-transparent hover:bg-surface-muted",
                        )}
                      >
                        <span className="flex items-start justify-between gap-3">
                          <span className="flex min-w-0 flex-wrap gap-1.5">
                            <Badge variant="outline">{kindLabels[entry.kind || ""] || "未知类型"}</Badge>
                            {ratingBadge(entry.rating)}
                            {statusBadge(entry.status)}
                          </span>
                          <time className="shrink-0 text-ui-xs text-muted-foreground" dateTime={dateTimeValue(entry.ts)}>{formatDate(entry.ts)}</time>
                        </span>
                        <span className="mt-3 block break-words text-ui-sm font-medium text-foreground" title={feedbackTitle(entry)}>{feedbackTitle(entry)}</span>
                        <span className="mt-1 line-clamp-2 whitespace-pre-wrap break-words text-ui-xs leading-relaxed text-muted-foreground">{feedbackSummary(entry)}</span>
                        <span className="mt-3 flex flex-wrap items-center justify-between gap-2 text-ui-xs text-muted-foreground">
                          <span>{entry.category || (entry.kind === "citation" ? "引用来源" : "回答问题")}</span>
                          <span>{entry.assignee_name || "未领取"}</span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ol>
            </section>
            <section aria-label="反馈详情" className="hidden min-w-0 xl:block">
              {detail}
            </section>
          </div>
        )}

        {!loading && response.total > 0 && (
          <div className="flex flex-col gap-2 border-t border-border px-4 py-3 text-ui-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-5">
            <span>共 {response.total} 条反馈，第 {response.page} / {totalPages} 页</span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</Button>
              <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</Button>
            </div>
          </div>
        )}
      </Card>

      <Sheet open={mobileDetailOpen && Boolean(selectedEntry)} onOpenChange={setMobileDetailOpen}>
        <SheetContent className="xl:hidden">
          <SheetHeader>
            <SheetTitle>反馈详情</SheetTitle>
            <SheetDescription className="line-clamp-2">{selectedEntry ? feedbackTitle(selectedEntry) : "查看反馈内容与处理状态"}</SheetDescription>
          </SheetHeader>
          <div className="min-h-0 flex-1 overflow-y-auto">{detail}</div>
        </SheetContent>
      </Sheet>

      <Dialog open={Boolean(completionTarget)} onOpenChange={(open) => { if (!open && !busyId) setCompletionTarget(null); }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>完成反馈处理</DialogTitle>
            <DialogDescription>{completionTarget ? `记录“${feedbackTitle(completionTarget)}”的处理结果。` : "记录处理结果。"}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <label className="space-y-1 text-ui-sm">
              <span className="font-medium text-foreground">处理结果</span>
              <Select aria-label="处理结果" value={resolution} onChange={(event) => setResolution(event.target.value as Resolution)}>
                {Object.entries(resolutionLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </Select>
            </label>
            <label className="space-y-1 text-ui-sm">
              <span className="font-medium text-foreground">处理备注</span>
              <textarea
                aria-label="处理备注"
                value={adminNote}
                onChange={(event) => setAdminNote(event.target.value)}
                maxLength={2000}
                placeholder="可选：记录具体修改或判断依据"
                className="min-h-28 w-full rounded-ui-md border border-input bg-background px-3 py-2 text-ui-sm text-foreground shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                disabled={Boolean(busyId)}
              />
            </label>
          </div>
          <DialogFooter className="flex-col-reverse sm:flex-row">
            <Button variant="outline" disabled={Boolean(busyId)} onClick={() => setCompletionTarget(null)}>取消</Button>
            <Button
              disabled={!completionTarget || Boolean(busyId)}
              onClick={() => completionTarget && void patch(completionTarget, "resolved", { resolution, admin_note: adminNote.trim() })}
            >
              {busyId ? "提交中…" : "确认完成"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Sheet open={conversationOpen} onOpenChange={setConversationOpen}>
        <SheetContent closeLabel="关闭完整对话">
          <SheetHeader>
            <SheetTitle>完整对话</SheetTitle>
            <SheetDescription>只读查看这条反馈所在对话的完整消息。</SheetDescription>
          </SheetHeader>
          <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
            <AdminConversationDetail
              conversation={conversation}
              loading={conversationLoading}
              error={conversationError}
              emptyTitle="没有可显示的对话"
              emptyDescription="这条反馈没有关联到可读取的对话记录。"
            />
          </div>
        </SheetContent>
      </Sheet>
    </section>
  );
}
