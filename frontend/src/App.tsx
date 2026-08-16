import { useEffect, useState } from "react";
import { Navigate, Route, BrowserRouter as Router, Routes, useNavigate } from "react-router-dom";
import { ChatLayout } from "./components/ChatLayout";
import { PdfPreview } from "./components/PdfPreview";
import { VideoPlayerDrawer } from "./components/VideoPlayerDrawer";
import { PdfPreviewProvider } from "./hooks/usePdfPreview";
import { VideoPlayerProvider } from "./hooks/useVideoPlayer";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
import { AdminCategoriesPage } from "./pages/admin/AdminCategoriesPage";
import { AdminConversationsPage } from "./pages/admin/AdminConversationsPage";
import { AdminDocumentsPage } from "./pages/admin/AdminDocumentsPage";
import { AdminFeedbackPage } from "./pages/admin/AdminFeedbackPage";
import { AdminLayout } from "./pages/admin/AdminLayout";
import { AdminMaintenancePage } from "./pages/admin/AdminMaintenancePage";
import { AdminManagedContentPage } from "./pages/admin/AdminManagedContentPage";
import { AdminMediaPage } from "./pages/admin/AdminMediaPage";
import { AdminOverviewPage } from "./pages/admin/AdminOverviewPage";
import { AdminUsersPage } from "./pages/admin/AdminUsersPage";
import { Toaster } from "./components/ui/toast";
import { hasContentWorkspaceAccess } from "./lib/workspace-access";

function FullPageLoader({ label }: { label: string }) {
  return (
    <div className="h-full flex items-center justify-center text-muted text-sm">
      {label}
    </div>
  );
}

function RequireAuth({ children }: { children: JSX.Element }) {
  const { state } = useAuth();
  if (state.status === "loading") return <FullPageLoader label="正在恢复登录…" />;
  if (state.status !== "authed") return <Navigate to="/login" replace />;
  return children;
}

function RequireAdmin({ children }: { children: JSX.Element }) {
  const { state, refreshUser } = useAuth();
  const [access, setAccess] = useState<"checking" | "allowed" | "denied">("checking");

  useEffect(() => {
    let cancelled = false;
    setAccess("checking");
    refreshUser()
      .then((user) => {
        if (!cancelled) setAccess(user && hasContentWorkspaceAccess(user) ? "allowed" : "denied");
      })
      .catch(() => {
        if (!cancelled) setAccess("denied");
      });
    return () => {
      cancelled = true;
    };
  }, [refreshUser]);

  if (state.status === "loading") return <FullPageLoader label="正在恢复登录…" />;
  if (state.status !== "authed") return <Navigate to="/login" replace />;
  if (access === "checking") return <FullPageLoader label="正在核对工作台权限…" />;
  if (access === "denied") return <Navigate to="/" replace />;
  return children;
}

function RedirectIfAuthed({ children }: { children: JSX.Element }) {
  const { state } = useAuth();
  if (state.status === "loading") return <FullPageLoader label="加载中…" />;
  if (state.status === "authed") return <Navigate to="/" replace />;
  return children;
}

function AdminIndexRedirect() {
  const { state } = useAuth();
  const target = state.status === "authed" && state.user.role === "admin" ? "overview" : "content";
  return <Navigate to={target} replace />;
}

function AdminOverviewRoute() {
  const navigate = useNavigate();
  return <AdminOverviewPage onOpenMaintenance={() => navigate("/admin/maintenance")} />;
}

export default function App() {
  return (
    <Router>
      <AuthProvider>
        <VideoPlayerProvider>
          <PdfPreviewProvider>
            <Routes>
              <Route
                path="/login"
                element={
                  <RedirectIfAuthed>
                    <LoginPage />
                  </RedirectIfAuthed>
                }
              />
              <Route
                path="/register"
                element={
                  <RedirectIfAuthed>
                    <RegisterPage />
                  </RedirectIfAuthed>
                }
              />
              <Route
                path="/admin"
                element={
                  <RequireAdmin>
                    <AdminLayout />
                  </RequireAdmin>
                }
              >
                <Route index element={<AdminIndexRedirect />} />
                <Route path="overview" element={<AdminOverviewRoute />} />
                <Route path="maintenance" element={<AdminMaintenancePage />} />
                <Route path="content" element={<AdminManagedContentPage />} />
                <Route path="categories" element={<AdminCategoriesPage />} />
                <Route path="media" element={<AdminMediaPage />} />
                <Route path="index" element={<AdminDocumentsPage />} />
                <Route path="users" element={<AdminUsersPage />} />
                <Route path="conversations" element={<AdminConversationsPage />} />
                <Route path="feedback" element={<AdminFeedbackPage />} />
                <Route path="*" element={<Navigate to="/admin" replace />} />
              </Route>
              <Route
                path="/"
                element={
                  <RequireAuth>
                    <ChatLayout />
                  </RequireAuth>
                }
              />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
            <PdfPreview />
          </PdfPreviewProvider>
          <VideoPlayerDrawer />
          <Toaster />
        </VideoPlayerProvider>
      </AuthProvider>
    </Router>
  );
}
