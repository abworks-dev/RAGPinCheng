import { useEffect, useState } from "react";
import { AppBrand } from "../../components/AppBrand";
import { ThemeMenu } from "../../components/ThemeMenu";
import { UserMenu } from "../../components/UserMenu";
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
  const [tab, setTab] = useState<Tab>("users");

  useEffect(() => {
    document.documentElement.classList.add("admin-scrollbar-stable");
    return () => document.documentElement.classList.remove("admin-scrollbar-stable");
  }, []);

  return (
    <div className="min-h-full bg-admin-background text-foreground">
      <header className="sticky top-0 z-sticky bg-admin-surface/95 backdrop-blur">
        <div className="flex min-h-16 items-center px-4 sm:px-6 lg:px-8">
          <AppBrand subtitle="管理工作台" subtitleClassName="hidden sm:block" />
        </div>
      </header>

      <div className="flex flex-col lg:min-h-[calc(100vh-4rem)] lg:flex-row">
        <aside className="flex shrink-0 flex-col bg-sidebar text-sidebar-foreground lg:sticky lg:top-16 lg:h-[calc(100vh-4rem)] lg:w-[17rem] lg:self-start">
          <div className="px-3 py-3 lg:p-4 lg:pb-3">
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
          <div className="mt-auto space-y-1 px-2 py-2">
            <ThemeMenu />
            <UserMenu adminContext />
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
