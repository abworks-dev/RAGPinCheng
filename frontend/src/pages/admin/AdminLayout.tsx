import { useEffect, useState } from "react";
import {
  FileText,
  LayoutDashboard,
  Menu,
  MessageSquareQuote,
  MessagesSquare,
  PanelLeftClose,
  SlidersHorizontal,
  Tags,
  Users,
  Video,
  Wrench,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { AppBrand } from "../../components/AppBrand";
import { IconButton } from "../../components/ui/icon-button";
import { ThemeMenu } from "../../components/ThemeMenu";
import { UserMenu } from "../../components/UserMenu";
import { cn } from "../../lib/utils";
import { useAuth } from "../../context/AuthContext";
import { contentWorkspaceTabs, workspaceLabel } from "../../lib/workspace-access";
import { Navigate, NavLink, Outlet, useLocation } from "react-router-dom";

type Tab = "users" | "conversations" | "managed" | "categories" | "media" | "stats" | "feedback" | "maintenance" | "answer-policy";

type TabDefinition = { key: Tab; label: string; path: string };

type NavigationGroup = {
  label: string;
  tabs: TabDefinition[];
};

const navigationIcons: Record<Tab, LucideIcon> = {
  stats: LayoutDashboard,
  maintenance: Wrench,
  "answer-policy": SlidersHorizontal,
  managed: FileText,
  categories: Tags,
  media: Video,
  users: Users,
  conversations: MessagesSquare,
  feedback: MessageSquareQuote,
};

const adminNavigation: NavigationGroup[] = [
  { label: "总览", tabs: [
    { key: "stats", label: "系统概览", path: "overview" },
    { key: "maintenance", label: "系统维护", path: "maintenance" },
    { key: "answer-policy", label: "回答策略", path: "answer-policy" },
  ] },
  {
    label: "内容管理",
    tabs: [
      { key: "managed", label: "资料管理", path: "content" },
      { key: "categories", label: "分类管理", path: "categories" },
      { key: "media", label: "视频管理", path: "media" },
    ],
  },
  {
    label: "运营管理",
    tabs: [
      { key: "users", label: "用户管理", path: "users" },
      { key: "conversations", label: "对话记录", path: "conversations" },
      { key: "feedback", label: "用户反馈", path: "feedback" },
    ],
  },
];

const adminTabByKey = new Map(adminNavigation.flatMap((group) => group.tabs).map((tab) => [tab.key, tab]));

export function AdminLayout() {
  const { state, refreshUser } = useAuth();
  const location = useLocation();
  const user = state.status === "authed" ? state.user : null;
  const isAdmin = !user?.role || user.role === "admin";
  const permissions = user?.content_permissions || [];
  const navigation: NavigationGroup[] = isAdmin
    ? adminNavigation
    : permissions.length > 0 ? [{
        label: "内容管理",
        tabs: contentWorkspaceTabs(permissions).map((key) => adminTabByKey.get(key)!),
      }] : [];
  const tabs = navigation.flatMap((group) => group.tabs);
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

  const currentPath = location.pathname.replace(/^\/admin\/?/, "").split("/")[0];
  const currentTab = tabs.find((tab) => tab.path === currentPath);
  if (user && tabs.length === 0) return <Navigate to="/" replace />;
  if (user && currentPath === "index" && (isAdmin || permissions.includes("index.view"))) {
    return <Navigate to="/admin/content?view=index" replace />;
  }
  if (user && currentPath && !currentTab) return <Navigate to={`/admin/${tabs[0].path}`} replace />;

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
                  {group.tabs.map(({ key, label, path }) => {
                    const Icon = navigationIcons[key];
                    return (
                      <NavLink
                        key={key}
                        to={`/admin/${path}`}
                        end
                        onClick={() => setMobileNavigationOpen(false)}
                        title={sidebarCollapsed ? label : undefined}
                        className={({ isActive }) => cn(
                          "flex h-control-md min-w-0 items-center rounded-ui-lg text-left text-ui-sm font-medium transition-colors duration-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          sidebarCollapsed ? "lg:w-9 lg:justify-center lg:px-0" : "gap-3 px-3",
                          isActive
                            ? "bg-primary text-primary-foreground shadow-surface"
                            : "text-muted-foreground hover:bg-surface-muted hover:text-foreground",
                        )}
                      >
                        <Icon className="size-4 shrink-0" aria-hidden="true" />
                        <span className={cn("min-w-0 whitespace-normal", sidebarCollapsed && "lg:hidden")}>{label}</span>
                      </NavLink>
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
          <Outlet />
        </div>
      </main>
    </div>
  );
}
