import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { useAuth } from "./lib/auth";
import { Dashboard } from "./pages/Dashboard";
import { Playground } from "./pages/Playground";
import { Requests } from "./pages/Requests";
import { Workspace } from "./pages/Workspace";
import { Account } from "./pages/Account";
import { Onboarding } from "./pages/Onboarding";
import { Login } from "./pages/Login";

function Loading() {
  return <div className="p-6 text-sm text-fg-subtle">Loading…</div>;
}

function RequireAuth({ children }: { children: ReactNode }) {
  const { authenticated, loading } = useAuth();
  if (loading) return <Loading />;
  return authenticated ? <>{children}</> : <Navigate to="/login" replace />;
}

function RequireWorkspace({ children }: { children: ReactNode }) {
  const { authenticated, inWorkspace, loading } = useAuth();
  if (loading) return <Loading />;
  if (!authenticated) return <Navigate to="/login" replace />;
  return inWorkspace ? <>{children}</> : <Navigate to="/welcome" replace />;
}

function RequireAdmin({ children }: { children: ReactNode }) {
  const { isWorkspaceAdmin, loading } = useAuth();
  if (loading) return <Loading />;
  return isWorkspaceAdmin ? <>{children}</> : <Navigate to="/dashboard" replace />;
}

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/login" element={<Login />} />
        <Route
          path="/welcome"
          element={
            <RequireAuth>
              <Onboarding />
            </RequireAuth>
          }
        />
        <Route
          path="/dashboard"
          element={
            <RequireWorkspace>
              <Dashboard />
            </RequireWorkspace>
          }
        />
        <Route
          path="/requests"
          element={
            <RequireWorkspace>
              <RequireAdmin>
                <Requests />
              </RequireAdmin>
            </RequireWorkspace>
          }
        />
        <Route
          path="/playground"
          element={
            <RequireWorkspace>
              <RequireAdmin>
                <Playground />
              </RequireAdmin>
            </RequireWorkspace>
          }
        />
        <Route
          path="/workspace"
          element={
            <RequireWorkspace>
              <RequireAdmin>
                <Workspace />
              </RequireAdmin>
            </RequireWorkspace>
          }
        />
        <Route
          path="/account"
          element={
            <RequireAuth>
              <Account />
            </RequireAuth>
          }
        />
        <Route path="/admin" element={<Navigate to="/workspace" replace />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </Layout>
  );
}
