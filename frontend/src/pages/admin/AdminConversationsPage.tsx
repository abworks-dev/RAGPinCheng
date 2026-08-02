import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { AdminConversation, ConversationState } from "../../types";
import { formatAdminDate } from "./admin-formatters";
export function AdminConversationsPage() {
  const [list, setList] = useState<AdminConversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<ConversationState | null>(null);
  const [error, setError] = useState<string | null>(null);
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

  return (
    <div className="space-y-3 h-[calc(100vh-220px)] flex flex-col">
      <div className="flex items-center gap-3">
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="按姓名、用户名或对话标题筛选…"
          className="w-80 rounded-lg border border-gray-300 px-3 py-1.5 text-sm bg-bg"
        />
        <span className="text-xs text-muted">
          {filter ? `${visible.length} / ${list.length}` : `${list.length} 条对话`}
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
      <div className="grid grid-cols-12 gap-4 flex-1 min-h-0">
        <div className="col-span-5 overflow-y-auto rounded-lg border border-gray-200 bg-panel">
          {error && <div className="p-3 text-sm text-red-600">{error}</div>}
          {loading && <div className="p-3 text-sm text-muted">加载中…</div>}
          {!loading && visible.length === 0 && (
            <div className="p-3 text-xs text-muted">
              {filter ? `没有匹配 “${filter}” 的对话` : "（暂无对话）"}
            </div>
          )}
          {visible.map((c) => (
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
              <div className="truncate font-medium">{c.title}</div>
              <div className="text-[11px] text-muted">
                {c.real_name} · {c.employee_id} · {formatAdminDate(c.updated_at)} · {c.turn_index} 轮
              </div>
            </button>
          ))}
        </div>
        <div className="col-span-7 overflow-y-auto rounded-lg border border-gray-200 bg-panel p-3 space-y-3">
          {!selected && <div className="text-sm text-muted">从左侧选择一条对话查看消息。</div>}
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
  );
}
