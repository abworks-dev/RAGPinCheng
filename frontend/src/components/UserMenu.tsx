import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { ChevronUp, LogOut, Shield } from "lucide-react";

export function UserMenu({
  collapsed = false,
  adminContext = false,
}: {
  collapsed?: boolean;
  adminContext?: boolean;
}) {
  const { state, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!ref.current) return;
      if (!ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  if (state.status !== "authed") return null;
  const u = state.user;
  const initials = (u.real_name || u.employee_id).slice(0, 1);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title={collapsed ? u.real_name : undefined}
        className={`flex h-10 items-center rounded-ui-md hover:bg-secondary ${collapsed ? "w-10 justify-center p-1" : "w-full gap-3 px-2"}`}
      >
        <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent text-white text-sm font-semibold">
          {initials}
        </span>
        {!collapsed && <div className="flex-1 min-w-0 text-left">
          <div className="text-sm truncate">{u.real_name}</div>
          <div className="text-[11px] text-muted truncate">用户名 {u.employee_id}</div>
        </div>}
        {!collapsed && <ChevronUp className={`size-4 text-muted-foreground transition-transform ${open ? "" : "rotate-180"}`} />}
      </button>
      {open && (
        <div className={`absolute bottom-12 z-dropdown rounded-ui-md border border-border bg-popover p-1.5 text-popover-foreground shadow-overlay ${collapsed ? "left-12 w-48" : "left-0 right-0"}`}>
          {u.role === "admin" && (
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                navigate("/");
              }}
              className="flex w-full items-center gap-2 rounded-ui-sm px-3 py-2 text-left text-sm hover:bg-secondary"
            >
              <ArrowLeft className="size-4" />
              返回对话
            </button>
          ) : (
            u.role === "admin" && (
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  navigate("/admin");
                }}
                className="flex w-full items-center gap-2 rounded-ui-sm px-3 py-2 text-left text-sm hover:bg-secondary"
              >
                <Shield className="size-4" />
                管理后台
              </button>
            )
          )}
          <button
            type="button"
            onClick={async () => {
              setOpen(false);
              await logout();
              navigate("/login");
            }}
            className="flex w-full items-center gap-2 rounded-ui-sm px-3 py-2 text-left text-sm text-destructive hover:bg-destructive/10"
          >
            <LogOut className="size-4" />
            退出登录
          </button>
        </div>
      )}
    </div>
  );
}
