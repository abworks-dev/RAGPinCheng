import { useEffect, useState } from "react";
import { Navigate, Route, BrowserRouter as Router, Routes } from "react-router-dom";
import { ChatLayout } from "./components/ChatLayout";
import { VideoPlayerDrawer } from "./components/VideoPlayerDrawer";
import { VideoPlayerProvider } from "./hooks/useVideoPlayer";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { AdminDashboard } from "./pages/AdminDashboard";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
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

export default function App() {
  return (
    <Router>
      <AuthProvider>
        <VideoPlayerProvider>
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
                  <AdminDashboard />
                </RequireAdmin>
              }
            />
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
          <VideoPlayerDrawer />
          <Toaster />
        </VideoPlayerProvider>
      </AuthProvider>
    </Router>
  );
}
