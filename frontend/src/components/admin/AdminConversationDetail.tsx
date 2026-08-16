import { Badge } from "../ui/badge";
import { EmptyState } from "../ui/empty-state";
import { ErrorState } from "../ui/error-state";
import { LoadingState } from "../ui/loading-state";
import { cn } from "../../lib/utils";
import type { ConversationState } from "../../types";
import { formatAdminDate } from "../../lib/admin-formatters";

const roleLabels: Record<ConversationState["messages"][number]["role"], string> = {
  user: "用户",
  assistant: "助手",
  system: "系统",
};

export type AdminConversationDetailProps = {
  conversation: ConversationState | null;
  loading?: boolean;
  error?: string | null;
  emptyTitle?: string;
  emptyDescription?: string;
};

export function AdminConversationDetail({
  conversation,
  loading = false,
  error = null,
  emptyTitle = "选择一条对话",
  emptyDescription = "从列表选择对话后，可在这里查看完整消息。",
}: AdminConversationDetailProps) {
  if (error) return <ErrorState title="对话详情加载失败" description={error} />;
  if (loading) return <LoadingState className="min-h-48" label="正在加载对话详情…" />;
  if (!conversation) {
    return <EmptyState className="min-h-56 border-0 bg-surface-muted" title={emptyTitle} description={emptyDescription} />;
  }

  return (
    <div className="space-y-5">
      <div className="border-b border-border pb-4">
        <h3 className="text-ui-lg font-semibold text-foreground">{conversation.title}</h3>
        <p className="mt-1 text-ui-xs text-muted-foreground">
          创建于 {formatAdminDate(conversation.created_at)} · 更新于 {formatAdminDate(conversation.updated_at)} · {conversation.turn_index} 轮
        </p>
      </div>

      <div className="space-y-3">
        {conversation.messages.map((message, index) => {
          const isUser = message.role === "user";
          const userVersions = message.user_versions || [];
          const pairedAnswer = isUser && conversation.messages[index + 1]?.role === "assistant"
            ? conversation.messages[index + 1]
            : null;
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
                {isUser && userVersions.length > 1 && (
                  <Badge variant="secondary">已编辑 · 版本 {userVersions.find((version) => version.is_active)?.version_index || userVersions.length}</Badge>
                )}
                {message.created_at && (
                  <span className="text-ui-xs text-muted-foreground">{formatAdminDate(message.created_at)}</span>
                )}
              </div>
              <p className="max-w-[72ch] whitespace-pre-wrap break-words text-ui-sm leading-relaxed text-foreground">
                {message.content}
              </p>
              {isUser && userVersions.length > 1 && (
                <details className="mt-3 border-t border-primary/15 pt-3">
                  <summary className="cursor-pointer text-ui-xs font-medium text-primary">
                    查看编辑记录（{userVersions.length} 个版本）
                  </summary>
                  <div className="mt-3 space-y-3">
                    {userVersions.map((version) => {
                      const linkedAnswers = pairedAnswer?.answer_versions?.filter(
                        (answer) => answer.user_version_id === version.id
                          || (version.version_index === 1 && answer.user_version_id == null),
                      ) || [];
                      return (
                        <div key={version.id} className="rounded-ui-md border border-border bg-card p-3">
                          <div className="flex flex-wrap items-center gap-2 text-ui-xs text-muted-foreground">
                            <span>问题版本 {version.version_index}</span>
                            {version.is_active && <Badge variant="info">当前</Badge>}
                            <span>{formatAdminDate(version.created_at)}</span>
                          </div>
                          <p className="mt-2 whitespace-pre-wrap break-words text-ui-sm text-foreground">
                            {version.content}
                          </p>
                          {linkedAnswers.length > 0 && (
                            <div className="mt-3 space-y-2 border-t border-border pt-2">
                              {linkedAnswers.map((answer) => (
                                <div key={answer.id}>
                                  <p className="text-ui-xs font-medium text-muted-foreground">
                                    对应回答版本 {answer.version_index}{answer.is_active ? " · 当前" : ""}
                                  </p>
                                  <p className="mt-1 whitespace-pre-wrap break-words text-ui-xs leading-relaxed text-foreground">
                                    {answer.content}
                                  </p>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </details>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
