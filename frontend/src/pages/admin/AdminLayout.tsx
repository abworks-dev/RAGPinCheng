import { useEffect, useState } from "react";
import { Menu, PanelLeftClose, X } from "lucide-react";
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
import { AdminCategoriesPage } from "./AdminCategoriesPage";
import { AdminManagedContentPage } from "./AdminManagedContentPage";
import { AdminMaintenancePage } from "./AdminMaintenancePage";
import { useAuth } from "../../context/AuthContext";
import { contentWorkspaceTabs, workspaceLabel } from "../../lib/workspace-access";
import { useNavigate, useSearchParams } from "react-router-dom";

type Tab = "users" | "conversations" | "corpus" | "managed" | "categories" | "media" | "stats" | "feedback" | "maintenance";

type NavigationGroup = {
  label: string;
  tabs: [Tab, string][];
};

const adminNavigation: NavigationGroup[] = [
  { label: "总览", tabs: [["stats", "概览"], ["maintenance", "系统维护"]] },
  {
    label: "内容管理",
    tabs: [
      ["managed", "资料管理"],
      ["categories", "分类管理"],
      ["media", "视频管理"],
      ["corpus", "索引任务"],
    ],
  },
  {
    label: "运营管理",
    tabs: [
      ["users", "用户管理"],
      ["conversations", "对话记录"],
      ["feedback", "用户反馈"],
    ],
  },
];

export function AdminLayout() {
  const { state, refreshUser } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const user = state.status === "authed" ? state.user : null;
  const isAdmin = !user?.role || user.role === "admin";
  const permissions = user?.content_permissions || [];
  const navigation: NavigationGroup[] = isAdmin
    ? adminNavigation
    : [{
        label: "内容管理",
        tabs: contentWorkspaceTabs(permissions).map((key) => [
          key,
          key === "managed" ? "资料管理" : "分类管理",
        ] as [Tab, string]),
      }];
  const tabs = navigation.flatMap((group) => group.tabs);
  const defaultTab: Tab = isAdmin ? "stats" : "managed";
  const requestedTab = searchParams.get("tab");
  const tab = tabs.some(([key]) => key === requestedTab)
    ? (requestedTab as Tab)
    : defaultTab;
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);

  useEffect(() => {
    document.documentElement.classList.add("admin-scrollbar-stable");
    return () => document.documentElement.classList.remove("admin-scrollbar-stable");
  }, []);

  useEffect(() => {
    const refresh = () => {
      void refreshUser().catch(() => {
        // Retain the current view when the network is temporarily unavailable.
      });
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") refresh();
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [refreshUser]);

  useEffect(() => {
    if (!user) return;
    if (tabs.length === 0) {
      navigate("/", { replace: true });
      return;
    }
    if (requestedTab && !tabs.some(([key]) => key === requestedTab)) {
      const nextParams = new URLSearchParams(searchParams);
      nextParams.delete("tab");
      setSearchParams(nextParams, { replace: true });
    }
  }, [navigate, requestedTab, searchParams, setSearchParams, tabs, user]);

  return (
    <div className="flex min-h-full flex-col bg-admin-background text-foreground lg:h-screen lg:flex-row lg:overflow-hidden">
      <aside
        className={cn(
          "flex shrink-0 flex-col bg-sidebar text-sidebar-foreground lg:h-full lg:transition-[width] lg:duration-normal",
          sidebarCollapsed ? "lg:w-16" : "lg:w-[17rem]",
        )}
      >
        <div className="border-b border-sidebar-border px-3 py-3 lg:border-b-0">
          <div className="flex h-9 items-center justify-between">
            <button
              type="button"
              aria-label="展开管理侧栏"
              title="展开管理侧栏"
              onClick={() => setSidebarCollapsed(false)}
              className={cn("hidden size-9 items-center justify-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", sidebarCollapsed && "lg:flex")}
            >
              <AppBrand subtitle={user ? workspaceLabel(user) : "管理工作台"} collapsed />
            </button>
            <div className={cn("flex min-w-0 flex-1 items-center justify-between", sidebarCollapsed && "lg:hidden")}>
                <AppBrand subtitle={user ? workspaceLabel(user) : "管理工作台"} />
                <div className="hidden lg:block">
                  <IconButton label="收起管理侧栏" onClick={() => setSidebarCollapsed(true)}>
                    <PanelLeftClose className="size-4" />
                  </IconButton>
                </div>
                <IconButton
                  className="lg:hidden"
                  label={mobileNavigationOpen ? "收起管理功能" : "展开管理功能"}
                  onClick={() => setMobileNavigationOpen((open) => !open)}
                >
                  {mobileNavigationOpen ? <X className="size-4" /> : <Menu className="size-4" />}
                </IconButton>
            </div>
          </div>
        </div>

        <div className={cn("px-3 py-3 lg:min-h-0 lg:flex-1", !mobileNavigationOpen && "hidden lg:block")}>
          <nav aria-label="管理功能" className="space-y-3">
            {navigation.map((group) => (
              <section key={group.label} aria-labelledby={`admin-nav-${group.label}`}>
                <p
                  id={`admin-nav-${group.label}`}
                  className={cn(
                    "mb-1 px-3 text-ui-xs font-medium text-muted-foreground",
                    sidebarCollapsed && "lg:sr-only",
                  )}
                >
                  {group.label}
                </p>
                <div className="grid grid-cols-2 gap-1 sm:grid-cols-3 lg:flex lg:flex-col">
                  {group.tabs.map(([key, label]) => {
                    const active = tab === key;
                    return (
                      <button
                        key={key}
                        type="button"
                        onClick={() => {
                          const nextParams = new URLSearchParams(searchParams);
                          if (key === defaultTab) nextParams.delete("tab");
                          else nextParams.set("tab", key);
                          setSearchParams(nextParams);
                          setMobileNavigationOpen(false);
                        }}
                        aria-current={active ? "page" : undefined}
                        title={sidebarCollapsed ? label : undefined}
                        className={cn(
                          "flex h-control-md min-w-0 items-center rounded-ui-lg text-left text-ui-sm font-medium transition-colors duration-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
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
                        <span className={cn("min-w-0 whitespace-normal", sidebarCollapsed && "lg:hidden")}>{label}</span>
                      </button>
                    );
                  })}
                </div>
              </section>
            ))}
          </nav>
        </div>
        <div className={cn("mt-auto hidden space-y-1 py-2 lg:block", sidebarCollapsed ? "px-3" : "px-2")}>
          <ThemeMenu collapsed={sidebarCollapsed} />
          <UserMenu adminContext collapsed={sidebarCollapsed} />
        </div>
      </aside>

      <main className="min-w-0 flex-1 p-4 sm:p-6 lg:overflow-y-auto lg:p-8 lg:[scrollbar-gutter:stable]">
        <div className="mx-auto max-w-7xl">
          {tab === "users" && <AdminUsersPage />}
          {tab === "conversations" && <AdminConversationsPage />}
          {tab === "corpus" && <AdminDocumentsPage />}
          {tab === "managed" && <AdminManagedContentPage />}
          {tab === "categories" && <AdminCategoriesPage />}
          {tab === "media" && <AdminMediaPage />}
          {tab === "stats" && <AdminOverviewPage />}
          {tab === "feedback" && <AdminFeedbackPage />}
          {tab === "maintenance" && <AdminMaintenancePage />}
        </div>
      </main>
    </div>
  );
}
