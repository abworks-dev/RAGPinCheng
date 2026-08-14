import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ApiError,
  api,
  setContentPermissionForbiddenHandler,
  setCsrfToken,
  setUnauthorizedHandler,
} from "../api/client";
import type { AuthUser } from "../types";

type AuthState =
  | { status: "loading"; user: null }
  | { status: "anon"; user: null }
  | { status: "authed"; user: AuthUser };

type AuthContextValue = {
  state: AuthState;
  login: (employee_id: string, password: string) => Promise<void>;
  register: (employee_id: string, real_name: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<AuthUser | null>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading", user: null });
  const refreshPromiseRef = useRef<Promise<AuthUser | null> | null>(null);

  const becomeAuthed = useCallback((user: AuthUser) => {
    setCsrfToken(user.csrf_token);
    setState({ status: "authed", user });
  }, []);

  const becomeAnon = useCallback(() => {
    setCsrfToken(null);
    setState({ status: "anon", user: null });
  }, []);

  const refreshUser = useCallback((): Promise<AuthUser | null> => {
    if (refreshPromiseRef.current) return refreshPromiseRef.current;

    const request = api.me()
      .then((user) => {
        becomeAuthed(user);
        return user;
      })
      .catch((error) => {
        if (error instanceof ApiError && error.status === 401) {
          becomeAnon();
          return null;
        }
        throw error;
      })
      .finally(() => {
        refreshPromiseRef.current = null;
      });
    refreshPromiseRef.current = request;
    return request;
  }, [becomeAnon, becomeAuthed]);

  // Bootstrap: ask backend who we are. 401 → anon.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await refreshUser();
      } catch (e) {
        if (!cancelled) {
          if (e instanceof ApiError && e.status === 401) {
            becomeAnon();
          } else {
            // Backend down: don't pretend we're authed. UI shows the login
            // page and the next call will surface the network error.
            becomeAnon();
          }
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [becomeAnon, refreshUser]);

  // Any 401 from a downstream call clears the session.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      becomeAnon();
    });
    return () => setUnauthorizedHandler(null);
  }, [becomeAnon]);

  useEffect(() => {
    setContentPermissionForbiddenHandler(() => {
      void refreshUser().catch(() => {
        // Keep the last trusted identity on transient network failures.
      });
    });
    return () => setContentPermissionForbiddenHandler(null);
  }, [refreshUser]);

  const login = useCallback(
    async (employee_id: string, password: string) => {
      const user = await api.login(employee_id, password);
      becomeAuthed(user);
    },
    [becomeAuthed],
  );

  const register = useCallback(
    async (employee_id: string, real_name: string, password: string) => {
      const user = await api.register(employee_id, real_name, password);
      becomeAuthed(user);
    },
    [becomeAuthed],
  );

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      /* even if backend errors, clear local state */
    }
    becomeAnon();
  }, [becomeAnon]);

  const value = useMemo<AuthContextValue>(
    () => ({ state, login, register, logout, refreshUser }),
    [state, login, register, logout, refreshUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
