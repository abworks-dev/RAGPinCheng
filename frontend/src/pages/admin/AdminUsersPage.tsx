import { useCallback, useEffect, useMemo, useState } from "react";
import { X } from "lucide-react";
import { api } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorState } from "../../components/ui/error-state";
import { Input } from "../../components/ui/input";
import { LoadingState } from "../../components/ui/loading-state";
import { cn } from "../../lib/utils";
import type { AdminConversation, AdminUser, ConversationState } from "../../types";
import { formatAdminDate } from "./admin-formatters";

const roleLabels: Record<ConversationState["messages"][number]["role"], string> = {
  user: "用户",
  assistant: "助手",
  system: "系统",
};

export function AdminUsersPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [drillUser, setDrillUser] = useState<AdminUser | null>(null);
  const [filter, setFilter] = useState("");

  // Filter on both real_name and employee_id since they live in the same
  // column visually and admins will sometimes search by either.
  const visibleUsers = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return users;
    return users.filter(
      (u) =>
        u.real_name.toLowerCase().includes(q) ||
        u.employee_id.toLowerCase().includes(q),
    );
  }, [users, filter]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { users } = await api.adminListUsers();
      setUsers(users);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function toggleActive(u: AdminUser) {
    try {
      await api.adminPatchUser(u.id, { is_active: !u.is_active });
      refresh();
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }

  async function toggleRole(u: AdminUser) {
    const newRole = u.role === "admin" ? "user" : "admin";
    if (!confirm(`将 ${u.real_name}（${u.employee_id}）的角色改为 ${newRole}？`)) return;
    try {
      await api.adminPatchUser(u.id, { role: newRole });
      refresh();
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }

  async function resetPw(u: AdminUser) {
    const pw = prompt(`为 ${u.real_name}（${u.employee_id}）设置新密码（≥ 6 位）：`);
    if (!pw) return;
    if (pw.length < 6) {
      alert("密码至少 6 位");
      return;
    }
    try {
      await api.adminPatchUser(u.id, { reset_password: pw });
      alert("密码已重置；该用户的所有会话已失效。");
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }

  return (
    <section className="space-y-5" aria-labelledby="admin-users-title">
      <header>
        <p className="text-ui-xs font-medium uppercase tracking-[0.14em] text-primary">账号与权限</p>
        <h1 id="admin-users-title" className="mt-1 text-ui-2xl font-semibold tracking-tight text-foreground">
          用户管理
        </h1>
        <p className="mt-1 text-ui-sm text-muted-foreground">查看账号状态、角色与使用情况，并执行受控的管理员操作。</p>
      </header>

      <Card className="shadow-surface">
        <CardContent className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between sm:p-4">
          <div className="w-full sm:max-w-xl">
            <label htmlFor="user-filter" className="sr-only">
              筛选用户
            </label>
            <Input
              id="user-filter"
              type="search"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="输入姓名或用户名…"
            />
          </div>
          <div className="flex min-h-control-md shrink-0 items-center gap-3">
            <span className="text-ui-xs text-muted-foreground" aria-live="polite">
              {filter ? `显示 ${visibleUsers.length} / ${users.length} 位` : `共 ${users.length} 位用户`}
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
        <ErrorState title="用户列表加载失败" description={error} />
      ) : loading ? (
        <Card>
          <LoadingState className="min-h-56" label="正在加载用户…" />
        </Card>
      ) : users.length === 0 ? (
        <EmptyState title="暂无用户" description="当前还没有可供管理员查看的用户账号。" />
      ) : visibleUsers.length === 0 ? (
        <EmptyState
          title="没有匹配的用户"
          description={`没有找到与“${filter}”匹配的姓名或用户名。`}
          action={
            <Button variant="outline" size="sm" onClick={() => setFilter("")}>
              清空筛选
            </Button>
          }
        />
      ) : (
        <Card className="overflow-hidden shadow-surface">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[58rem] text-ui-sm">
              <caption className="sr-only">用户账号、角色、状态、使用情况和管理员操作</caption>
              <thead className="border-b border-border bg-surface-muted text-muted-foreground">
                <tr>
                  <th scope="col" className="px-4 py-3 text-left font-medium">用户</th>
                  <th scope="col" className="px-4 py-3 text-left font-medium">角色</th>
                  <th scope="col" className="px-4 py-3 text-left font-medium">状态</th>
                  <th scope="col" className="px-4 py-3 text-right font-medium">对话</th>
                  <th scope="col" className="hidden px-4 py-3 text-left font-medium lg:table-cell">最近登录</th>
                  <th scope="col" className="hidden px-4 py-3 text-left font-medium xl:table-cell">注册时间</th>
                  <th scope="col" className="px-4 py-3 text-left font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {visibleUsers.map((user) => (
                  <tr key={user.id} className="bg-card transition-colors duration-normal hover:bg-surface-muted/60">
                    <td className="px-4 py-3">
                      <div className="font-medium text-foreground">{user.real_name}</div>
                      <div className="mt-0.5 font-mono text-ui-xs text-muted-foreground">{user.employee_id}</div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={user.role === "admin" ? "info" : "secondary"}>
                        {user.role === "admin" ? "管理员" : "用户"}
                      </Badge>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={user.is_active ? "success" : "destructive"}>
                        {user.is_active ? "启用" : "已停用"}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Button
                        variant="link"
                        size="sm"
                        className="h-auto px-0"
                        aria-label={`查看 ${user.real_name} 的 ${user.conversation_count} 条对话`}
                        onClick={() => setDrillUser(user)}
                      >
                        {user.conversation_count} 条
                      </Button>
                    </td>
                    <td className="hidden whitespace-nowrap px-4 py-3 text-muted-foreground lg:table-cell">
                      {formatAdminDate(user.last_login_at)}
                    </td>
                    <td className="hidden whitespace-nowrap px-4 py-3 text-muted-foreground xl:table-cell">
                      {formatAdminDate(user.created_at)}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          className={cn(
                            "shadow-sm",
                            user.is_active
                              ? "border-destructive/50 bg-destructive/10 text-destructive hover:border-destructive/70 hover:bg-destructive/20 hover:text-destructive"
                              : "border-success/50 bg-success/10 text-success hover:border-success/70 hover:bg-success/20 hover:text-success",
                          )}
                          onClick={() => toggleActive(user)}
                        >
                          {user.is_active ? "停用账号" : "启用账号"}
                        </Button>
                        <Button variant="outline" size="sm" className="bg-card shadow-sm" onClick={() => toggleRole(user)}>
                          {user.role === "admin" ? "降为用户" : "设为管理员"}
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          className="border border-border shadow-sm"
                          onClick={() => resetPw(user)}
                        >
                          重置密码
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {drillUser && (
        <UserConversationsDrillIn
          user={drillUser}
          onClose={() => setDrillUser(null)}
        />
      )}
    </section>
  );
}

function UserConversationsDrillIn({
  user,
  onClose,
}: {
  user: AdminUser;
  onClose: () => void;
}) {
  const [list, setList] = useState<AdminConversation[]>([]);
  const [selected, setSelected] = useState<ConversationState | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const { conversations } = await api.adminListUserConversations(user.id);
        setList(conversations);
      } finally {
        setLoading(false);
      }
    })();
  }, [user.id]);

  return (
    <div className="fixed inset-0 z-modal flex items-stretch justify-center bg-black/50 p-3 backdrop-blur-sm sm:p-6">
      <Card
        className="flex max-h-[calc(100vh-1.5rem)] w-full max-w-6xl flex-col overflow-hidden shadow-overlay sm:max-h-[calc(100vh-3rem)]"
        role="dialog"
        aria-modal="true"
        aria-labelledby="user-conversations-title"
      >
        <div className="flex items-center justify-between gap-4 border-b border-border px-4 py-3 sm:px-5">
          <div className="min-w-0">
            <h2 id="user-conversations-title" className="truncate text-ui-base font-semibold text-foreground">
              {user.real_name}的对话
            </h2>
            <p className="truncate text-ui-xs text-muted-foreground">用户名 {user.employee_id} · 只读查看</p>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="关闭用户对话">
            <X className="size-4" aria-hidden="true" />
          </Button>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[18rem_minmax(0,1fr)]">
          <div className="max-h-56 overflow-y-auto border-b border-border md:max-h-none md:border-b-0 md:border-r">
            {loading ? (
              <LoadingState className="min-h-32" label="正在加载对话…" />
            ) : list.length === 0 ? (
              <EmptyState className="m-3 border-0 bg-surface-muted" title="暂无对话" description="该用户尚无对话记录。" />
            ) : (
              <div className="divide-y divide-border" role="list">
                {list.map((conversation) => (
                  <div key={conversation.id} role="listitem">
                    <button
                      type="button"
                      onClick={async () => {
                        try {
                          const state = await api.adminGetConversation(conversation.id);
                          setSelected(state);
                        } catch (e: any) {
                          alert(e?.message || String(e));
                        }
                      }}
                      className={cn(
                        "w-full border-l-2 px-4 py-3 text-left transition-colors duration-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                        selected?.id === conversation.id
                          ? "border-l-primary bg-primary/10"
                          : "border-l-transparent hover:bg-surface-muted",
                      )}
                    >
                      <p className="truncate text-ui-sm font-medium text-foreground">{conversation.title}</p>
                      <p className="mt-1 text-ui-xs text-muted-foreground">{formatAdminDate(conversation.updated_at)}</p>
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="min-h-0 overflow-y-auto p-4 sm:p-5">
            {!selected ? (
              <EmptyState
                className="min-h-56 border-0 bg-surface-muted"
                title="选择一条对话"
                description="从列表选择对话后，可在这里查看完整消息。"
              />
            ) : (
              <div className="space-y-4">
                <div className="border-b border-border pb-3">
                  <h3 className="text-ui-lg font-semibold text-foreground">{selected.title}</h3>
                  <p className="mt-1 text-ui-xs text-muted-foreground">{selected.turn_index} 轮对话</p>
                </div>
                {selected.messages.map((message, index) => {
                  const isUser = message.role === "user";
                  return (
                    <article
                      key={message.id ?? `${message.role}-${index}`}
                      className={cn(
                        "rounded-ui-xl border px-4 py-3",
                        isUser
                          ? "ml-auto w-fit max-w-[88%] border-primary/20 bg-primary/10"
                          : "mr-auto w-full max-w-3xl border-border bg-surface-muted",
                      )}
                    >
                      <Badge
                        variant={message.role === "user" ? "info" : message.role === "system" ? "warning" : "outline"}
                        className={message.role === "assistant" ? "border-border bg-card" : undefined}
                      >
                        {roleLabels[message.role]}
                      </Badge>
                      <p className="mt-2 max-w-[72ch] whitespace-pre-wrap break-words text-ui-sm leading-relaxed text-foreground">
                        {message.content}
                      </p>
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}
