import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Card } from "../components/Card";

export function Onboarding() {
  const { inWorkspace, refresh } = useAuth();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<null | "create" | "join">(null);
  const [err, setErr] = useState<string | null>(null);

  if (inWorkspace) return <Navigate to="/dashboard" replace />;

  async function run(kind: "create" | "join", fn: () => Promise<unknown>) {
    setBusy(kind);
    setErr(null);
    try {
      await fn();
      await refresh();
      navigate("/dashboard", { replace: true });
    } catch (e) {
      setErr((e as Error).message);
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-lg font-semibold text-fg">Get started</h1>
      <p className="mb-4 mt-1 text-sm text-fg-muted">
        Create a workspace for your team, or join one you were invited to.
      </p>
      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Create a workspace">
          <p className="mb-3 text-sm text-fg-muted">
            You become the <strong>Workspace Admin</strong> — issue virtual keys, invite
            Team Members, and see workspace-wide usage.
          </p>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              void run("create", () => api.createWorkspace(name.trim()));
            }}
          >
            <label className="block text-xs font-medium text-fg-muted">
              Workspace name
              <input
                required
                minLength={1}
                maxLength={80}
                className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
                placeholder="Acme Inc"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <button
              type="submit"
              disabled={busy !== null || !name.trim()}
              className="w-full rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
            >
              {busy === "create" ? "Creating…" : "Create workspace"}
            </button>
          </form>
        </Card>

        <Card title="Join a workspace">
          <p className="mb-3 text-sm text-fg-muted">
            Paste the invite code your Workspace Admin sent you. You join as a
            <strong> Team Member</strong> and see only the keys assigned to you.
          </p>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              void run("join", () => api.joinWorkspace(code.trim()));
            }}
          >
            <label className="block text-xs font-medium text-fg-muted">
              Invite code
              <input
                required
                minLength={1}
                maxLength={64}
                className="mt-1 w-full rounded-md border border-line px-3 py-2 font-mono text-sm"
                placeholder="paste code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
              />
            </label>
            <button
              type="submit"
              disabled={busy !== null || !code.trim()}
              className="w-full rounded-md border border-line px-3 py-2 text-sm font-medium text-fg hover:bg-fill disabled:opacity-50"
            >
              {busy === "join" ? "Joining…" : "Join workspace"}
            </button>
          </form>
        </Card>
      </div>
      {err && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-400">
          {err}
        </div>
      )}
    </div>
  );
}
