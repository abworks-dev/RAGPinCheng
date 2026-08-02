import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { AdminConversationsPage } from "./AdminConversationsPage";
import { AdminDocumentsPage } from "./AdminDocumentsPage";
import { AdminFeedbackPage } from "./AdminFeedbackPage";
import { AdminMediaPage } from "./AdminMediaPage";
import { AdminOverviewPage } from "./AdminOverviewPage";
import { AdminUsersPage } from "./AdminUsersPage";

type Tab = "users" | "conversations" | "corpus" | "media" | "stats" | "feedback";

export function AdminLayout() {
  const { state, logout } = useAuth();
  const [tab, setTab] = useState<Tab>("users");

  return (
    <div className="h-full flex flex-col bg-bg">
      <header className="border-b border-gray-200 bg-panel px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-lg">🛠️</span>
          <h1 className="font-semibold">管理后台 · 品成 BIM 知识库</h1>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <Link to="/" className="text-accent hover:underline">
            ← 返回对话
          </Link>
          {state.status === "authed" && (
            <span className="text-muted">
              {state.user.real_name}（{state.user.employee_id}）
            </span>
          )}
          <button
            type="button"
            onClick={async () => {
              await logout();
              window.location.href = "/login";
            }}
            className="text-muted hover:text-red-600"
          >
            退出
          </button>
        </div>
      </header>

      <div className="border-b border-gray-200 bg-panel px-6">
        <nav className="flex gap-1">
          {([
            ["users", "用户"],
            ["conversations", "对话"],
            ["corpus", "资料管理"],
            ["media", "视频媒体"],
            ["stats", "概览"],
            ["feedback", "反馈"],
          ] as [Tab, string][]).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              className={
                "px-4 py-2 text-sm border-b-2 -mb-px " +
                (tab === key
                  ? "border-accent text-accent"
                  : "border-transparent text-muted hover:text-ink")
              }
            >
              {label}
            </button>
          ))}
        </nav>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {tab === "users" && <AdminUsersPage />}
        {tab === "conversations" && <AdminConversationsPage />}
        {tab === "corpus" && <AdminDocumentsPage />}
        {tab === "media" && <AdminMediaPage />}
        {tab === "stats" && <AdminOverviewPage />}
        {tab === "feedback" && <AdminFeedbackPage />}
      </div>
    </div>
  );
}
