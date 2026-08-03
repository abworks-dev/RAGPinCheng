import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { Input } from "../../components/ui/input";
import { LoadingState } from "../../components/ui/loading-state";
import { cn } from "../../lib/utils";
import type { AdminConversation, ConversationState } from "../../types";
import { formatAdminDate } from "./admin-formatters";

const roleLabels: Record<ConversationState["messages"][number]["role"], string> = {
  user: "用户",
  assistant: "助手",
  system: "系统",
};

export function AdminConversationsPage() {
  const [list, setList] = useState<AdminConversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<ConversationState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [loadingConversationId, setLoadingConversationId] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const { conversations } = await api.adminListAllConversations(200);
        setList(conversations);
      } catch (e: any) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  // Filter on user name, employee_id, and conversation title — admins
  // browsing for "what was this person asking about?" benefit from matching
  // either the user or the topic.
  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return list;
    return list.filter(
      (c) =>
        c.real_name.toLowerCase().includes(q) ||
        c.employee_id.toLowerCase().includes(q) ||
        c.title.toLowerCase().includes(q),
    );
  }, [list, filter]);

  async function selectConversation(conversation: AdminConversation) {
    setDetailError(null);
    setLoadingConversationId(conversation.id);
    try {
      const state = await api.adminGetConversation(conversation.id);
      setSelected(state);
    } catch (e: any) {
      setDetailError(e?.message || String(e));
    } finally {
      setLoadingConversationId(null);
    }
  }

  return (
    <section className="space-y-5" aria-labelledby="admin-conversations-title">
      <header>
        <p className="text-ui-xs font-medium uppercase tracking-[0.14em] text-primary">内容审阅</p>
        <h1 id="admin-conversations-title" className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">
          对话管理
        </h1>
        <p className="mt-1 text-ui-sm text-muted-foreground">按用户或主题查找近期对话，并在详情区查看完整消息。</p>
      </header>

      <Card className="shadow-surface">
        <CardContent className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between sm:p-4">
          <div className="w-full sm:max-w-xl">
            <label htmlFor="conversation-filter" className="sr-only">
              筛选对话
            </label>
            <Input
              id="conversation-filter"
              type="search"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="输入姓名、用户名或对话标题…"
            />
          </div>
          <div className="flex min-h-control-md shrink-0 items-center gap-3">
            <span className="text-ui-xs text-muted-foreground" aria-live="polite">
              {filter ? `显示 ${visible.length} / ${list.length} 条` : `共 ${list.length} 条`}
            </span>
            {filter && (
              <Button variant="ghost" size="sm" onClick={() => setFilter("")}>
                清空筛选
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {error ? (
        <ErrorState title="对话列表加载失败" description={error} />
      ) : loading ? (
        <Card>
          <LoadingState className="min-h-56" label="正在加载对话…" />
        </Card>
      ) : list.length === 0 ? (
        <EmptyState title="暂无对话" description="当前还没有可供管理员查看的对话记录。" />
      ) : (
        <div className="grid gap-4 xl:h-[calc(100vh-17rem)] xl:min-h-[34rem] xl:grid-cols-[minmax(19rem,0.9fr)_minmax(0,1.5fr)]">
          <Card className="flex min-h-0 flex-col overflow-hidden shadow-surface">
            <div className="border-b border-border px-4 py-3">
              <h2 className="text-ui-sm font-semibold text-foreground">对话列表</h2>
              <p className="text-ui-xs text-muted-foreground">按最近更新时间排列</p>
            </div>

            {visible.length === 0 ? (
              <EmptyState
                className="m-4 flex-1 border-0 bg-surface-muted"
                title="没有匹配的对话"
                description={`没有找到与“${filter}”匹配的姓名、用户名或标题。`}
                action={
                  <Button variant="outline" size="sm" onClick={() => setFilter("")}>
                    清空筛选
                  </Button>
                }
              />
            ) : (
              <div className="max-h-[28rem] divide-y divide-border overflow-y-auto xl:max-h-none xl:flex-1" role="list">
                {visible.map((conversation) => {
                  const active = selected?.id === conversation.id;
                  const detailLoading = loadingConversationId === conversation.id;
                  return (
                    <div key={conversation.id} role="listitem">
                      <button
                        type="button"
                        aria-pressed={active}
                        onClick={() => selectConversation(conversation)}
                        disabled={loadingConversationId !== null}
                        className={cn(
                          "w-full border-l-2 px-4 py-3 text-left transition-colors duration-normal focus-visible:relative focus-visible:z-base focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:cursor-wait disabled:opacity-70",
                          active
                            ? "border-l-primary bg-primary/10"
                            : "border-l-transparent bg-card hover:bg-surface-muted",
                        )}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="truncate text-ui-sm font-medium text-foreground">{conversation.title}</p>
                            <p className="mt-1 truncate text-ui-xs text-muted-foreground">
                              {conversation.real_name} · {conversation.employee_id}
                            </p>
                          </div>
                          <Badge variant={active ? "info" : "secondary"} className="shrink-0">
                            {conversation.turn_index} 轮
                          </Badge>
                        </div>
                        <p className="mt-2 text-ui-xs text-muted-foreground">
                          {detailLoading ? "正在载入详情…" : `更新于 ${formatAdminDate(conversation.updated_at)}`}
                        </p>
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </Card>

          <Card className="flex min-h-[28rem] min-w-0 flex-col overflow-hidden shadow-surface xl:min-h-0">
            <div className="border-b border-border px-4 py-3 sm:px-5">
              <h2 className="text-ui-sm font-semibold text-foreground">对话详情</h2>
              <p className="text-ui-xs text-muted-foreground">只读查看用户与助手的消息内容</p>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-5">
              {detailError ? (
                <ErrorState title="对话详情加载失败" description={detailError} />
              ) : loadingConversationId ? (
                <LoadingState className="min-h-48" label="正在加载对话详情…" />
              ) : !selected ? (
                <EmptyState
                  className="min-h-56 border-0 bg-surface-muted"
                  title="选择一条对话"
                  description="从左侧列表选择对话后，可在这里查看完整消息。"
                />
              ) : (
                <div className="space-y-5">
                  <div className="border-b border-border pb-4">
                    <h3 className="text-ui-lg font-semibold text-foreground">{selected.title}</h3>
                    <p className="mt-1 text-ui-xs text-muted-foreground">
                      创建于 {formatAdminDate(selected.created_at)} · 更新于 {formatAdminDate(selected.updated_at)} · {selected.turn_index} 轮
                    </p>
                  </div>

                  <div className="space-y-3">
                    {selected.messages.map((message, index) => {
                      const isUser = message.role === "user";
                      return (
                        <article
                          key={message.id ?? `${message.role}-${index}`}
                          className={cn(
                            "rounded-ui-xl border px-4 py-3",
                            isUser
                              ? "ml-auto w-fit max-w-[88%] border-primary/20 bg-primary/10"
                              : "mr-auto w-fit max-w-3xl border-border bg-surface-muted",
                          )}
                        >
                          <div className="mb-2 flex items-center justify-between gap-3">
                            <Badge
                              variant={message.role === "user" ? "info" : message.role === "system" ? "warning" : "outline"}
                              className={message.role === "assistant" ? "border-border bg-card" : undefined}
                            >
                              {roleLabels[message.role]}
                            </Badge>
                            {message.created_at && (
                              <span className="text-ui-xs text-muted-foreground">{formatAdminDate(message.created_at)}</span>
                            )}
                          </div>
                          <p className="max-w-[72ch] whitespace-pre-wrap break-words text-ui-sm leading-relaxed text-foreground">
                            {message.content}
                          </p>
                        </article>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          </Card>
        </div>
      )}
    </section>
  );
}
