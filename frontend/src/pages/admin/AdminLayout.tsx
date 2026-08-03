import { useState } from "react";
import { Link } from "react-router-dom";
import { AppBrand } from "../../components/AppBrand";
import { Button, buttonVariants } from "../../components/ui/button";
import { useAuth } from "../../context/AuthContext";
import { cn } from "../../lib/utils";
import { AdminConversationsPage } from "./AdminConversationsPage";
import { AdminDocumentsPage } from "./AdminDocumentsPage";
import { AdminFeedbackPage } from "./AdminFeedbackPage";
import { AdminMediaPage } from "./AdminMediaPage";
import { AdminOverviewPage } from "./AdminOverviewPage";
import { AdminUsersPage } from "./AdminUsersPage";

type Tab = "users" | "conversations" | "corpus" | "media" | "stats" | "feedback";

const tabs: [Tab, string][] = [
  ["users", "用户"],
  ["conversations", "对话"],
  ["corpus", "资料管理"],
  ["media", "视频媒体"],
  ["stats", "概览"],
  ["feedback", "反馈"],
];

export function AdminLayout() {
  const { state, logout } = useAuth();
  const [tab, setTab] = useState<Tab>("users");

  return (
    <div className="min-h-full bg-admin-background text-foreground">
      <header className="sticky top-0 z-sticky bg-admin-surface/95 backdrop-blur">
        <div className="flex min-h-16 items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
          <AppBrand subtitle="管理工作台" subtitleClassName="hidden sm:block" />

          <div className="flex shrink-0 items-center gap-1 sm:gap-2">
            {state.status === "authed" && (
              <span className="hidden max-w-56 truncate text-ui-sm text-muted-foreground xl:inline">
                {state.user.real_name}（{state.user.employee_id}）
              </span>
            )}
            <Link to="/" className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "px-2 sm:px-3")}>
              <span aria-hidden="true">←</span>
              <span className="hidden sm:inline">返回对话</span>
              <span className="sm:hidden">返回</span>
            </Link>
            <Button
              variant="ghost"
              size="sm"
              className="px-2 text-muted-foreground hover:text-destructive sm:px-3"
              onClick={async () => {
                await logout();
                window.location.href = "/login";
              }}
            >
              退出
            </Button>
          </div>
        </div>
      </header>

      <div className="flex flex-col lg:min-h-[calc(100vh-4rem)] lg:flex-row">
        <aside className="shrink-0 bg-admin-surface lg:w-64">
          <div className="px-3 py-3 lg:p-4">
            <p className="mb-2 hidden px-3 text-ui-xs font-medium uppercase tracking-[0.14em] text-muted-foreground lg:block">
              管理功能
            </p>
            <nav aria-label="管理功能" className="flex gap-1 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible lg:pb-0">
              {tabs.map(([key, label]) => {
                const active = tab === key;
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setTab(key)}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex h-control-md shrink-0 items-center gap-3 rounded-ui-lg px-3 text-ui-sm font-medium transition-colors duration-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      active
                        ? "bg-primary text-primary-foreground shadow-surface"
                        : "text-muted-foreground hover:bg-surface-muted hover:text-foreground",
                    )}
                  >
                    <span
                      className={cn("h-2 w-2 rounded-full", active ? "bg-primary-foreground" : "bg-border")}
                      aria-hidden="true"
                    />
                    {label}
                  </button>
                );
              })}
            </nav>
          </div>
        </aside>

        <main className="min-w-0 flex-1 p-4 sm:p-6 lg:p-8">
          <div className="mx-auto max-w-7xl">
            {tab === "users" && <AdminUsersPage />}
            {tab === "conversations" && <AdminConversationsPage />}
            {tab === "corpus" && <AdminDocumentsPage />}
            {tab === "media" && <AdminMediaPage />}
            {tab === "stats" && <AdminOverviewPage />}
            {tab === "feedback" && <AdminFeedbackPage />}
          </div>
        </main>
      </div>
    </div>
  );
}
