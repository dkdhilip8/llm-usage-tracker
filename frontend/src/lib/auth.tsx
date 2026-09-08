import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, type AuthUser, type WorkspaceRef, type WorkspaceRole } from "./api";

interface AuthCtx {
  user: AuthUser | null;
  authenticated: boolean;
  workspace: WorkspaceRef | null;
  role: WorkspaceRole | null;
  inWorkspace: boolean;
  isWorkspaceAdmin: boolean;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  signup: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const me = await api.me();
      setUser(me.authenticated ? me.user : null);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (username: string, password: string) => {
    setUser((await api.login(username, password)).user);
  }, []);

  const signup = useCallback(async (username: string, password: string) => {
    setUser((await api.signup(username, password)).user);
  }, []);

  const logout = useCallback(async () => {
    await api.logout();
    setUser(null);
  }, []);

  const value = useMemo<AuthCtx>(() => {
    const workspace = user?.workspace ?? null;
    return {
      user,
      authenticated: user !== null,
      workspace,
      role: workspace?.role ?? null,
      inWorkspace: workspace !== null,
      isWorkspaceAdmin: workspace?.role === "admin",
      loading,
      login,
      signup,
      logout,
      refresh,
    };
  }, [user, loading, login, signup, logout, refresh]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be used within <AuthProvider>");
  return v;
}
