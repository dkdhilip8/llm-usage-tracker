import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { useAuth } from "./lib/auth";
import { Dashboard } from "./pages/Dashboard";
import { Playground } from "./pages/Playground";
import { Requests } from "./pages/Requests";
import { Account } from "./pages/Admin";
import { Login } from "./pages/Login";

function RequireAuth({ children }: { children: ReactNode }) {
  const { authenticated, loading } = useAuth();
  if (loading) {
    return <div className="p-6 text-sm text-fg-subtle">Loading…</div>;
  }
  return authenticated ? children : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route
          path="/dashboard"
          element={
            <RequireAuth>
              <Dashboard />
            </RequireAuth>
          }
        />
        <Route
          path="/requests"
          element={
            <RequireAuth>
              <Requests />
            </RequireAuth>
          }
        />
        <Route
          path="/playground"
          element={
            <RequireAuth>
              <Playground />
            </RequireAuth>
          }
        />
        <Route path="/login" element={<Login />} />
        <Route path="/account" element={<Account />} />
        <Route path="/admin" element={<Navigate to="/account" replace />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </Layout>
  );
}
