import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { AdminConversation, AdminUser, ConversationState } from "../../types";
import { formatAdminDate } from "./admin-formatters";
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
    <div className="space-y-4">
      {error && <div className="text-sm text-red-600">{error}</div>}
      <div className="flex items-center gap-3">
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="按姓名或用户名筛选…"
          className="w-72 rounded-lg border border-gray-300 px-3 py-1.5 text-sm bg-bg"
        />
        <span className="text-xs text-muted">
          {filter
            ? `${visibleUsers.length} / ${users.length}`
            : `${users.length} 位用户`}
        </span>
        {filter && (
          <button
            type="button"
            onClick={() => setFilter("")}
            className="text-xs text-accent hover:underline"
          >
            清空
          </button>
        )}
      </div>
      <div className="overflow-x-auto rounded-lg border border-gray-200 bg-panel">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-800 text-muted">
            <tr>
              <th className="text-left px-3 py-2">用户名</th>
              <th className="text-left px-3 py-2">姓名</th>
              <th className="text-left px-3 py-2">角色</th>
              <th className="text-left px-3 py-2">状态</th>
              <th className="text-right px-3 py-2">对话数</th>
              <th className="text-left px-3 py-2">最近登录</th>
              <th className="text-left px-3 py-2">注册时间</th>
              <th className="text-left px-3 py-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {visibleUsers.map((u) => (
              <tr key={u.id} className="border-t border-gray-100 dark:border-gray-800">
                <td className="px-3 py-2 font-mono">{u.employee_id}</td>
                <td className="px-3 py-2">{u.real_name}</td>
                <td className="px-3 py-2">
                  <span
                    className={
                      u.role === "admin"
                        ? "px-1.5 py-0.5 rounded text-xs bg-purple-100 text-purple-700"
                        : "px-1.5 py-0.5 rounded text-xs bg-gray-100 text-gray-700"
                    }
                  >
                    {u.role}
                  </span>
                </td>
                <td className="px-3 py-2">
                  {u.is_active ? (
                    <span className="text-green-600">启用</span>
                  ) : (
                    <span className="text-red-600">已停用</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right">
                  <button
                    type="button"
                    className="text-accent hover:underline"
                    onClick={() => setDrillUser(u)}
                  >
                    {u.conversation_count}
                  </button>
                </td>
                <td className="px-3 py-2 text-muted">{formatAdminDate(u.last_login_at)}</td>
                <td className="px-3 py-2 text-muted">{formatAdminDate(u.created_at)}</td>
                <td className="px-3 py-2 space-x-2 whitespace-nowrap">
                  <button
                    type="button"
                    className="text-xs text-accent hover:underline"
                    onClick={() => toggleActive(u)}
                  >
                    {u.is_active ? "停用" : "启用"}
                  </button>
                  <button
                    type="button"
                    className="text-xs text-accent hover:underline"
                    onClick={() => toggleRole(u)}
                  >
                    {u.role === "admin" ? "降为用户" : "升为管理员"}
                  </button>
                  <button
                    type="button"
                    className="text-xs text-accent hover:underline"
                    onClick={() => resetPw(u)}
                  >
                    重置密码
                  </button>
                </td>
              </tr>
            ))}
            {!loading && visibleUsers.length === 0 && (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-muted">
                  {filter
                    ? `没有匹配 “${filter}” 的用户`
                    : "（暂无用户）"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {drillUser && (
        <UserConversationsDrillIn
          user={drillUser}
          onClose={() => setDrillUser(null)}
        />
      )}
    </div>
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
    <div className="fixed inset-0 bg-black/40 flex items-stretch justify-center p-6 z-20">
      <div className="bg-panel rounded-xl w-full max-w-5xl flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
          <div className="text-sm">
            <span className="font-semibold">{user.real_name}</span>
            <span className="text-muted ml-2">用户名 {user.employee_id} 的对话</span>
          </div>
          <button onClick={onClose} className="text-muted hover:text-ink">
            ✕
          </button>
        </div>
        <div className="flex flex-1 min-h-0">
          <div className="w-72 border-r border-gray-200 overflow-y-auto">
            {loading && <div className="p-3 text-xs text-muted">加载中…</div>}
            {list.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={async () => {
                  try {
                    const state = await api.adminGetConversation(c.id);
                    setSelected(state);
                  } catch (e: any) {
                    alert(e?.message || String(e));
                  }
                }}
                className={
                  "w-full text-left px-3 py-2 text-sm border-b border-gray-100 dark:border-gray-800 " +
                  (selected?.id === c.id ? "bg-accent/10" : "hover:bg-gray-100 dark:hover:bg-gray-800")
                }
              >
                <div className="truncate">{c.title}</div>
                <div className="text-[11px] text-muted">{formatAdminDate(c.updated_at)}</div>
              </button>
            ))}
            {!loading && list.length === 0 && (
              <div className="p-3 text-xs text-muted">该用户尚无对话。</div>
            )}
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            {!selected && (
              <div className="text-sm text-muted">从左侧选择一条对话查看消息内容。</div>
            )}
            {selected?.messages.map((m, i) => (
              <div
                key={i}
                className={
                  "rounded-lg px-3 py-2 text-sm whitespace-pre-wrap " +
                  (m.role === "user"
                    ? "bg-accent/10"
                    : "bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700")
                }
              >
                <div className="text-[11px] text-muted mb-1">{m.role}</div>
                {m.content}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
