import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { Card } from "../components/Card";

export function Login() {
  const { login, signup, authenticated } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (authenticated) return <Navigate to="/account" replace />;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      if (mode === "signup") await signup(username.trim().toLowerCase(), password);
      else await login(username.trim().toLowerCase(), password);
      navigate("/account", { replace: true });
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-md">
      <Card title={mode === "signup" ? "Create an account" : "Sign in"}>
        <p className="mb-3 text-sm text-fg-muted">
          {mode === "signup"
            ? "Create your own virtual keys, mint them for teammates, and track usage and cost on a private dashboard."
            : "Sign in to manage your virtual keys, use the Playground, and see your usage and cost."}
        </p>
        <form onSubmit={submit} className="space-y-3">
          <label className="block text-xs font-medium text-fg-muted">
            Username
            <input
              type="text"
              autoComplete="username"
              required
              minLength={3}
              maxLength={32}
              pattern="[A-Za-z0-9][A-Za-z0-9._-]{1,30}[A-Za-z0-9]"
              title="3–32 characters: letters, digits, . _ - (must start and end with a letter or digit)"
              className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>
          <label className="block text-xs font-medium text-fg-muted">
            Password
            <input
              type="password"
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
              required
              minLength={8}
              className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {err && <div className="text-sm text-red-600 dark:text-red-400">{err}</div>}
          <button
            type="submit"
            disabled={busy || username.trim().length < 3 || password.length < 8}
            className="w-full rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {busy
              ? "Working…"
              : mode === "signup"
                ? "Sign up"
                : "Sign in"}
          </button>
        </form>
        <button
          onClick={() => {
            setMode(mode === "signup" ? "login" : "signup");
            setErr(null);
          }}
          className="mt-3 text-xs text-brand-600 hover:underline"
        >
          {mode === "signup"
            ? "Already have an account? Sign in"
            : "New here? Create an account"}
        </button>
        <p className="mt-3 text-[11px] text-fg-subtle">
          No password reset yet — use a unique password and pick a username you'll remember.
        </p>
      </Card>
    </div>
  );
}
