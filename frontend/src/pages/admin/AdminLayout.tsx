import { useEffect, useState } from "react";
import { PanelLeftClose } from "lucide-react";
import { AppBrand } from "../../components/AppBrand";
import { IconButton } from "../../components/ui/icon-button";
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
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  useEffect(() => {
    document.documentElement.classList.add("admin-scrollbar-stable");
    return () => document.documentElement.classList.remove("admin-scrollbar-stable");
  }, []);

  return (
    <div className="flex min-h-full flex-col bg-admin-background text-foreground lg:h-screen lg:flex-row lg:overflow-hidden">
      <aside
        className={cn(
          "flex shrink-0 flex-col bg-sidebar text-sidebar-foreground lg:h-full lg:transition-[width] lg:duration-normal",
          sidebarCollapsed ? "lg:w-16" : "lg:w-[17rem]",
        )}
      >
        <div className="px-3 py-3">
          <div className="flex h-9 items-center justify-between">
            {sidebarCollapsed ? (
              <button
                type="button"
                aria-label="展开管理侧栏"
                title="展开管理侧栏"
                onClick={() => setSidebarCollapsed(false)}
                className="hidden size-9 items-center justify-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:flex"
              >
                <AppBrand subtitle="管理工作台" collapsed />
              </button>
            ) : (
              <>
                <AppBrand subtitle="管理工作台" />
                <div className="hidden lg:block">
                  <IconButton label="收起管理侧栏" onClick={() => setSidebarCollapsed(true)}>
                    <PanelLeftClose className="size-4" />
                  </IconButton>
                </div>
              </>
            )}
          </div>
        </div>

        <div className="px-3 py-3 lg:min-h-0 lg:flex-1">
          {!sidebarCollapsed && (
            <p className="mb-2 hidden px-3 text-ui-xs font-medium uppercase tracking-[0.14em] text-muted-foreground lg:block">
              管理功能
            </p>
          )}
          <nav aria-label="管理功能" className="flex gap-1 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible lg:pb-0">
            {tabs.map(([key, label]) => {
              const active = tab === key;
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => setTab(key)}
                  aria-current={active ? "page" : undefined}
                  title={sidebarCollapsed ? label : undefined}
                  className={cn(
                    "flex h-control-md shrink-0 items-center rounded-ui-lg text-ui-sm font-medium transition-colors duration-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    sidebarCollapsed ? "lg:w-9 lg:justify-center lg:px-0" : "gap-3 px-3",
                    active
                      ? "bg-primary text-primary-foreground shadow-surface"
                      : "text-muted-foreground hover:bg-surface-muted hover:text-foreground",
                  )}
                >
                  <span
                    className={cn("h-2 w-2 shrink-0 rounded-full", active ? "bg-primary-foreground" : "bg-border")}
                    aria-hidden="true"
                  />
                  <span className={cn(sidebarCollapsed && "lg:hidden")}>{label}</span>
                </button>
              );
            })}
          </nav>
        </div>
        <div className={cn("mt-auto space-y-1 py-2", sidebarCollapsed ? "px-3" : "px-2")}>
          <ThemeMenu collapsed={sidebarCollapsed} />
          <UserMenu adminContext collapsed={sidebarCollapsed} />
        </div>
      </aside>

      <main className="min-w-0 flex-1 p-4 sm:p-6 lg:overflow-y-auto lg:p-8 lg:[scrollbar-gutter:stable]">
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
  );
}
