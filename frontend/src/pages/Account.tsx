import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Card } from "../components/Card";

export function Account() {
  const { authenticated, loading, user, workspace, role, logout, refresh } = useAuth();
  const navigate = useNavigate();
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  if (loading) {
    return <div className="mx-auto max-w-md p-6 text-sm text-fg-subtle">Loading…</div>;
  }
  if (!authenticated) return <Navigate to="/login" replace />;

  async function run(kind: string, fn: () => Promise<unknown>, after: () => void) {
    setBusy(kind);
    setMsg(null);
    try {
      await fn();
      after();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-lg space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-fg">Your account</h1>
        <button
          onClick={() => void logout()}
          className="text-sm text-fg-muted hover:underline"
        >
          Sign out
        </button>
      </div>

      <Card title="Identity">
        <dl className="space-y-1 text-sm">
          <div className="flex justify-between">
            <dt className="text-fg-muted">Username</dt>
            <dd className="font-medium">{user?.username}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-fg-muted">Workspace</dt>
            <dd className="font-medium">{workspace ? workspace.name : "—"}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-fg-muted">Role</dt>
            <dd className="font-medium">
              {role === "admin" ? "Workspace Admin" : role === "member" ? "Team Member" : "—"}
            </dd>
          </div>
        </dl>
      </Card>

      {workspace && role === "member" && (
        <Card title="Leave workspace">
          <p className="mb-2 text-xs text-fg-muted">
            You'll lose access to this workspace's data. Any keys assigned to you are
            unassigned (the admin keeps them).
          </p>
          <button
            onClick={() =>
              window.confirm(`Leave ${workspace.name}?`) &&
              void run(
                "leave",
                () => api.leaveWorkspace(),
                async () => {
                  await refresh();
                  navigate("/welcome", { replace: true });
                },
              )
            }
            disabled={busy !== null}
            className="rounded-md border border-line px-3 py-1.5 text-sm text-fg-muted hover:bg-fill disabled:opacity-50"
          >
            {busy === "leave" ? "Leaving…" : "Leave workspace"}
          </button>
        </Card>
      )}

      <Card title="Danger zone">
        <p className="mb-2 text-xs text-fg-muted">
          Deleting your account is permanent. A Workspace Admin must remove all members
          and delete the workspace first.
        </p>
        <button
          onClick={() =>
            window.confirm("Permanently delete your account?") &&
            void run(
              "delete",
              () => api.deleteAccount(),
              () => void logout(),
            )
          }
          disabled={busy !== null}
          className="rounded-md border border-red-200 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 disabled:opacity-50 dark:border-red-500/30 dark:text-red-400 dark:hover:bg-red-500/10"
        >
          {busy === "delete" ? "Deleting…" : "Delete account"}
        </button>
      </Card>

      {msg && <div className="text-xs text-red-600 dark:text-red-400">{msg}</div>}
    </div>
  );
}
