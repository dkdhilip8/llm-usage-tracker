import { useCallback, useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import {
  api,
  PROVIDERS,
  type BudgetPeriod,
  type KeyCreated,
  type KeyRow,
  type WorkspaceDetail,
  type WorkspaceProvider,
} from "../lib/api";
import { useAuth } from "../lib/auth";
import { Card } from "../components/Card";
import { num, relTime, usd } from "../lib/format";

// ---------- provider keys + caps ----------
function ProviderRow({
  p,
  defaultCap,
  onChange,
}: {
  p: WorkspaceProvider;
  defaultCap: number;
  onChange: () => void;
}) {
  const [editing, setEditing] = useState(p.source === "none");
  const [val, setVal] = useState("");
  const [cap, setCap] = useState("");
  const [busy, setBusy] = useState<null | "key" | "cap">(null);
  const [err, setErr] = useState<string | null>(null);

  const run = async (kind: "key" | "cap", fn: () => Promise<unknown>) => {
    setBusy(kind);
    setErr(null);
    try {
      await fn();
      onChange();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const effCap = p.monthly_cap_usd ?? defaultCap;
  const over = p.spend_this_month >= effCap;

  return (
    <li className="space-y-1.5 border-b border-line pb-2 last:border-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-1.5 text-sm">
        <span className="font-medium capitalize">{p.provider}</span>
        {p.source === "env" ? (
          <span className="ml-auto rounded bg-fill px-1.5 py-0.5 text-[10px] text-fg-muted">
            set via server env
          </span>
        ) : (
          <div className="ml-auto flex items-center gap-1.5">
            {p.source === "workspace" && !editing && (
              <>
                <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400">
                  saved ····{p.last4}
                </span>
                <button
                  className="text-[11px] text-brand-600 hover:underline"
                  onClick={() => setEditing(true)}
                >
                  replace
                </button>
                <button
                  className="text-[11px] text-red-600 hover:underline disabled:opacity-50"
                  disabled={busy !== null}
                  onClick={() => run("key", () => api.clearWorkspaceProviderKey(p.provider))}
                >
                  clear
                </button>
              </>
            )}
            {editing && (
              <>
                <input
                  type="password"
                  placeholder={`paste ${p.provider} key`}
                  className="w-40 rounded border border-line px-1.5 py-0.5 text-[11px]"
                  value={val}
                  onChange={(e) => setVal(e.target.value)}
                />
                <button
                  className="rounded bg-brand-600 px-1.5 py-0.5 text-[10px] font-medium text-white hover:bg-brand-700 disabled:opacity-50"
                  disabled={busy !== null || val.trim().length < 8}
                  onClick={() =>
                    run("key", async () => {
                      await api.setWorkspaceProviderKey(p.provider, val.trim());
                      setVal("");
                      setEditing(false);
                    })
                  }
                >
                  save
                </button>
                {p.source === "workspace" && (
                  <button
                    className="text-[11px] text-fg-subtle hover:underline"
                    onClick={() => setEditing(false)}
                  >
                    cancel
                  </button>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {p.source === "workspace" && (
        <div className="pl-0.5">
          <div className="flex justify-between text-[11px] text-fg-subtle">
            <span>live spend this month</span>
            <span>
              {usd(p.spend_this_month)} / {usd(effCap)}
            </span>
          </div>
          <div className="mt-1 h-1.5 rounded bg-fill">
            <div
              className={`h-1.5 rounded ${over ? "bg-red-500" : "bg-brand-500"}`}
              style={{
                width: `${Math.min(100, effCap > 0 ? (p.spend_this_month / effCap) * 100 : 100)}%`,
              }}
            />
          </div>
          <div className="mt-1.5 flex items-center gap-1.5">
            <span className="text-[11px] text-fg-muted">Monthly cap $</span>
            <input
              type="number"
              min="0"
              step="1"
              className="w-24 rounded-md border border-line px-2 py-0.5 text-[11px]"
              placeholder={
                p.monthly_cap_usd != null ? String(p.monthly_cap_usd) : `${defaultCap} (default)`
              }
              value={cap}
              onChange={(e) => setCap(e.target.value)}
            />
            <button
              className="rounded-md border border-line px-2 py-0.5 text-[11px] text-fg-muted hover:bg-fill disabled:opacity-50"
              disabled={busy !== null || cap.trim() === ""}
              onClick={() =>
                run("cap", async () => {
                  await api.setWorkspaceProviderCap(p.provider, Number(cap));
                  setCap("");
                })
              }
            >
              set
            </button>
            {p.monthly_cap_usd != null && (
              <button
                className="text-[11px] text-brand-600 hover:underline disabled:opacity-50"
                disabled={busy !== null}
                onClick={() => run("cap", () => api.setWorkspaceProviderCap(p.provider, null))}
              >
                use default
              </button>
            )}
          </div>
        </div>
      )}
      {err && <div className="text-[10px] text-red-600">{err}</div>}
    </li>
  );
}

function ProvidersCard({
  providers,
  defaultCap,
  onChange,
}: {
  providers: WorkspaceProvider[];
  defaultCap: number;
  onChange: () => void;
}) {
  return (
    <Card title="Provider keys">
      <div className="space-y-3 text-sm">
        <p className="text-xs text-fg-muted">
          Attach the workspace's own OpenAI / Anthropic / OpenRouter / Gemini key and set a
          monthly cap. Virtual keys for a configured provider hit the real provider on this key,
          up to its cap. Only the last 4 digits are shown back.
        </p>
        <ul className="space-y-2">
          {providers.map((p) => (
            <ProviderRow key={p.provider} p={p} defaultCap={defaultCap} onChange={onChange} />
          ))}
        </ul>
      </div>
    </Card>
  );
}

// ---------- members + invites ----------
function MembersCard({
  ws,
  onChange,
}: {
  ws: WorkspaceDetail;
  onChange: () => void;
}) {
  const [label, setLabel] = useState("");
  const [minted, setMinted] = useState<{ code: string; label: string | null } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const run = async (kind: string, fn: () => Promise<unknown>) => {
    setBusy(kind);
    setErr(null);
    try {
      await fn();
      onChange();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const members = ws.members ?? [];
  const invites = ws.invites ?? [];

  return (
    <Card title={`Members (${members.length})`}>
      <div className="space-y-3">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-fg-subtle">
              <th className="py-1.5 pr-3">User</th>
              <th className="py-1.5 pr-3">Role</th>
              <th className="py-1.5 pr-3 text-right">Keys</th>
              <th className="py-1.5 pr-3 text-right">Requests</th>
              <th className="py-1.5 pr-0" />
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.user_id} className="border-b border-line last:border-0">
                <td className="py-1.5 pr-3 font-medium">{m.username}</td>
                <td className="py-1.5 pr-3 text-xs text-fg-muted">
                  {m.role === "admin" ? "Workspace Admin" : "Team Member"}
                </td>
                <td className="py-1.5 pr-3 text-right tabular-nums">{m.assigned_keys}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums">{num(m.requests)}</td>
                <td className="py-1.5 pr-0 text-right">
                  {m.role === "member" && (
                    <button
                      onClick={() =>
                        window.confirm(`Remove ${m.username} from the workspace?`) &&
                        void run("rm" + m.user_id, () => api.removeMember(m.user_id))
                      }
                      disabled={busy !== null}
                      className="rounded border border-line px-2 py-0.5 text-xs text-red-600 hover:bg-red-50 disabled:opacity-50 dark:text-red-400 dark:hover:bg-red-500/10"
                    >
                      Remove
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="border-t border-line pt-3">
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
            Invite a Team Member
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              className="flex-1 rounded-md border border-line px-2 py-1.5 text-sm"
              placeholder="label (optional) — e.g. Jane"
              maxLength={80}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
            />
            <button
              onClick={() =>
                void run("mint", async () => {
                  const inv = await api.createInvite(label);
                  setMinted({ code: inv.code, label: inv.label });
                  setLabel("");
                })
              }
              disabled={busy !== null}
              className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
            >
              Generate invite
            </button>
          </div>

          {minted && (
            <div className="mt-2 rounded-md border border-emerald-200 bg-emerald-50 p-2 text-xs dark:border-emerald-500/30 dark:bg-emerald-500/10">
              <div className="font-medium text-emerald-800 dark:text-emerald-300">
                Send this code to {minted.label ?? "your Team Member"} — it works once.
              </div>
              <div className="mt-1 flex items-center gap-2">
                <code className="flex-1 break-all rounded bg-surface px-2 py-1 font-mono">
                  {minted.code}
                </code>
                <button
                  onClick={() => navigator.clipboard?.writeText(minted.code)}
                  className="rounded bg-emerald-600 px-2 py-1 font-medium text-white hover:bg-emerald-700"
                >
                  Copy
                </button>
              </div>
            </div>
          )}

          {invites.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs">
              {invites.map((inv) => (
                <li
                  key={inv.id}
                  className="flex items-center gap-2 rounded bg-fill px-2 py-1"
                >
                  <code className="font-mono">{inv.code}</code>
                  {inv.label && <span className="text-fg-muted">· {inv.label}</span>}
                  <span className="text-fg-subtle">
                    · expires {new Date(inv.expires_at).toLocaleDateString()}
                  </span>
                  <button
                    onClick={() => void run("rev" + inv.id, () => api.revokeInvite(inv.id))}
                    disabled={busy !== null}
                    className="ml-auto text-brand-600 hover:underline disabled:opacity-50"
                  >
                    revoke
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        {err && <div className="text-xs text-red-600 dark:text-red-400">{err}</div>}
      </div>
    </Card>
  );
}

// ---------- create key ----------
function CreateKeyForm({
  onCreated,
  configuredProviders,
  members,
}: {
  onCreated: () => void;
  configuredProviders: string[];
  members: { user_id: number; username: string }[];
}) {
  const [label, setLabel] = useState("");
  const [allowed, setAllowed] = useState<string[]>([]);
  const [assignee, setAssignee] = useState("");
  const [budget, setBudget] = useState("");
  const [budgetPeriod, setBudgetPeriod] = useState<BudgetPeriod>("month");
  const [budgetStart, setBudgetStart] = useState("");
  const [budgetEnd, setBudgetEnd] = useState("");
  const [created, setCreated] = useState<KeyCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const canPick = useCallback(
    (p: string) => configuredProviders.includes(p),
    [configuredProviders],
  );
  const picked = allowed.filter(canPick);
  const noneConfigured = configuredProviders.length === 0;
  const willBeLive = picked.length > 0;

  useEffect(() => {
    setAllowed((a) => {
      const kept = a.filter(canPick);
      if (kept.length) return kept.length === a.length ? a : kept;
      const first = PROVIDERS.find(canPick);
      return first ? [first] : a.length ? [] : a;
    });
  }, [canPick]);

  function toggle(p: string) {
    if (!canPick(p)) return;
    setAllowed((a) => (a.includes(p) ? a.filter((x) => x !== p) : [...a, p]));
  }

  async function submit() {
    setErr(null);
    try {
      const res = await api.createKey({
        label: label.trim(),
        allowed_providers: picked,
        allow_live: willBeLive,
        default_provider: picked.length === 1 ? picked[0] : null,
        assigned_user_id: assignee ? Number(assignee) : null,
        monthly_budget_usd: budget.trim() ? Number(budget) : null,
        budget_period: budgetPeriod,
        budget_start: budgetPeriod === "custom" ? budgetStart || null : null,
        budget_end: budgetPeriod === "custom" ? budgetEnd || null : null,
      });
      setCreated(res);
      setCopied(false);
      setLabel("");
      setBudget("");
      setBudgetStart("");
      setBudgetEnd("");
      setAssignee("");
      onCreated();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  return (
    <Card title="Create a virtual key">
      <div className="space-y-3">
        <input
          className="w-full rounded-md border border-line px-3 py-2 text-sm"
          placeholder="Label — e.g. Jane (Data Science) or Support Bot"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />

        <div>
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
            Allowed providers
          </div>
          <div className="flex flex-wrap gap-3">
            {PROVIDERS.map((p) => {
              const ok = canPick(p);
              return (
                <label
                  key={p}
                  title={ok ? undefined : `No key configured for ${p}`}
                  className={`flex items-center gap-1.5 text-sm ${
                    ok ? "" : "cursor-not-allowed opacity-40"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={allowed.includes(p)}
                    disabled={!ok}
                    onChange={() => toggle(p)}
                  />
                  <span className="capitalize">{p}</span>
                </label>
              );
            })}
          </div>
          {noneConfigured && (
            <p className="mt-1 text-[11px] text-fg-subtle">
              No provider keys configured. Add one (a server env var, or under{" "}
              <strong>Provider keys</strong>) to create keys for it.
            </p>
          )}
        </div>

        <div className="grid grid-cols-2 gap-2">
          <label className="text-xs font-medium text-fg-muted">
            Assign to
            <select
              className="mt-1 w-full rounded-md border border-line px-2 py-1.5 text-sm"
              value={assignee}
              onChange={(e) => setAssignee(e.target.value)}
            >
              <option value="">— unassigned (admin only) —</option>
              {members.map((m) => (
                <option key={m.user_id} value={m.user_id}>
                  {m.username}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs font-medium text-fg-muted">
            Budget (USD)
            <input
              type="number"
              step="0.01"
              min="0"
              placeholder="unlimited"
              className="mt-1 w-full rounded-md border border-line px-2 py-1.5 text-sm"
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
            />
          </label>
        </div>

        <label className="block text-xs font-medium text-fg-muted">
          Budget period
          <select
            className="mt-1 w-full rounded-md border border-line px-2 py-1.5 text-sm"
            value={budgetPeriod}
            onChange={(e) => setBudgetPeriod(e.target.value as BudgetPeriod)}
          >
            <option value="day">1 day</option>
            <option value="week">1 week</option>
            <option value="month">1 month</option>
            <option value="custom">Custom range</option>
          </select>
        </label>

        {budgetPeriod === "custom" && (
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs font-medium text-fg-muted">
              Budget from
              <input
                type="date"
                className="mt-1 w-full rounded-md border border-line px-2 py-1.5 text-sm"
                value={budgetStart}
                onChange={(e) => setBudgetStart(e.target.value)}
              />
            </label>
            <label className="text-xs font-medium text-fg-muted">
              to (inclusive)
              <input
                type="date"
                min={budgetStart || undefined}
                className="mt-1 w-full rounded-md border border-line px-2 py-1.5 text-sm"
                value={budgetEnd}
                onChange={(e) => setBudgetEnd(e.target.value)}
              />
            </label>
          </div>
        )}

        <p className="text-[11px] text-fg-subtle">
          {willBeLive
            ? "This key makes live calls on the workspace's provider key, under that provider's monthly cap. Pause it any time from the table below."
            : "Add a configured provider above — until then this key runs simulated."}
        </p>

        <button
          onClick={submit}
          disabled={!label.trim() || picked.length === 0}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
        >
          Create key
        </button>

        {err && <div className="text-sm text-red-600 dark:text-red-400">{err}</div>}

        {created && (
          <div className="rounded-md border border-emerald-200 bg-emerald-50 p-3 dark:border-emerald-500/30 dark:bg-emerald-500/10">
            <div className="text-xs font-medium text-emerald-800 dark:text-emerald-300">
              Copy this key now — it is shown only once.
            </div>
            <div className="mt-1 flex items-center gap-2">
              <code className="flex-1 break-all rounded bg-surface px-2 py-1 font-mono text-xs text-fg">
                {created.key}
              </code>
              <button
                onClick={() => {
                  navigator.clipboard?.writeText(created.key);
                  setCopied(true);
                }}
                className="rounded bg-emerald-600 px-2 py-1 text-xs font-medium text-white hover:bg-emerald-700"
              >
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
            <div className="mt-1 text-xs text-emerald-700 dark:text-emerald-400">
              {created.label} · {created.allowed_providers.join(", ")}
              {created.allow_live ? " · live-allowed" : ""}
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

// ---------- settings ----------
function SettingsCard({ ws, onChange }: { ws: WorkspaceDetail; onChange: () => void }) {
  const { refresh: refreshAuth } = useAuth();
  const [name, setName] = useState(ws.name);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  return (
    <Card title="Workspace settings">
      <div className="space-y-3 text-sm">
        <div className="flex items-end gap-2">
          <label className="flex-1 text-xs font-medium text-fg-muted">
            Name
            <input
              className="mt-1 w-full rounded-md border border-line px-2 py-1.5 text-sm"
              value={name}
              maxLength={80}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <button
            onClick={async () => {
              setBusy("rename");
              setErr(null);
              try {
                await api.renameWorkspace(name.trim());
                await refreshAuth();
                onChange();
              } catch (e) {
                setErr((e as Error).message);
              } finally {
                setBusy(null);
              }
            }}
            disabled={busy !== null || !name.trim() || name.trim() === ws.name}
            className="rounded-md border border-line px-3 py-1.5 text-sm text-fg-muted hover:bg-fill disabled:opacity-50"
          >
            Rename
          </button>
        </div>
        <div className="border-t border-line pt-3">
          <p className="mb-2 text-xs text-fg-muted">
            Deleting the workspace removes all its virtual keys, usage, and provider keys.
            Remove every other member first.
          </p>
          <button
            onClick={async () => {
              if (!window.confirm(`Delete ${ws.name} and all its data?`)) return;
              setBusy("delete");
              setErr(null);
              try {
                await api.deleteWorkspace();
                await refreshAuth();
              } catch (e) {
                setErr((e as Error).message);
                setBusy(null);
              }
            }}
            disabled={busy !== null}
            className="rounded-md border border-red-200 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 disabled:opacity-50 dark:border-red-500/30 dark:text-red-400 dark:hover:bg-red-500/10"
          >
            {busy === "delete" ? "Deleting…" : "Delete workspace"}
          </button>
        </div>
        {err && <div className="text-xs text-red-600 dark:text-red-400">{err}</div>}
      </div>
    </Card>
  );
}

// ---------- page ----------
export function Workspace() {
  const { isWorkspaceAdmin, loading, logout } = useAuth();
  const [ws, setWs] = useState<WorkspaceDetail | null>(null);
  const [keys, setKeys] = useState<KeyRow[]>([]);
  const [defaultCap, setDefaultCap] = useState(5);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api.getWorkspace().then(setWs).catch((e: Error) => setError(e.message));
    api.listKeys().then(setKeys).catch((e: Error) => setError(e.message));
    api.getAccount().then((a) => setDefaultCap(a.live_cap_default_usd)).catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (loading) {
    return <div className="mx-auto max-w-md p-6 text-sm text-fg-subtle">Loading…</div>;
  }
  if (!isWorkspaceAdmin) return <Navigate to="/dashboard" replace />;

  const providers = ws?.providers ?? [];
  const configured = providers.filter((p) => p.configured).map((p) => p.provider);
  const members = (ws?.members ?? []).filter((m) => m.role === "member");
  const keyLiveReady = (k: KeyRow) => k.allowed_providers.some((p) => configured.includes(p));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-fg">{ws?.name ?? "Workspace"}</h1>
          <div className="text-xs text-fg-subtle">Workspace Admin</div>
        </div>
        <button
          onClick={() => {
            void logout();
            setKeys([]);
          }}
          className="text-sm text-fg-muted hover:underline"
        >
          Sign out
        </button>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-400">
          {error}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <div className="space-y-4">
          <ProvidersCard
            providers={providers}
            defaultCap={defaultCap}
            onChange={refresh}
          />
          {ws && <SettingsCard ws={ws} onChange={refresh} />}
        </div>
        <div className="space-y-4">
          <CreateKeyForm
            onCreated={refresh}
            configuredProviders={configured}
            members={members}
          />
          {ws && <MembersCard ws={ws} onChange={refresh} />}
        </div>
      </div>

      <Card title={`Virtual keys (${keys.length})`}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-fg-subtle">
                <th className="py-2 pr-4">Label</th>
                <th className="py-2 pr-4">Prefix</th>
                <th className="py-2 pr-4">Assigned</th>
                <th className="py-2 pr-4">Allowed providers</th>
                <th className="py-2 pr-4 text-center">Live</th>
                <th className="py-2 pr-4 text-right">Requests</th>
                <th className="py-2 pr-4 text-right">Cost</th>
                <th className="py-2 pr-4">Budget</th>
                <th className="py-2 pr-4">Last used</th>
                <th className="py-2 pr-0 text-right">Status</th>
              </tr>
            </thead>
            <tbody>
              {keys.length === 0 && (
                <tr>
                  <td colSpan={10} className="py-8 text-center text-fg-subtle">
                    No keys yet — create one above.
                  </td>
                </tr>
              )}
              {keys.map((k) => (
                <tr
                  key={k.id}
                  className={`border-b border-line last:border-0 ${
                    k.revoked_at ? "text-fg-subtle" : ""
                  }`}
                >
                  <td className="py-2 pr-4 font-medium">{k.label}</td>
                  <td className="py-2 pr-4 font-mono text-xs">{k.key_prefix}…</td>
                  <td className="py-2 pr-4 text-xs text-fg-subtle">
                    {k.assigned_username ?? "—"}
                  </td>
                  <td className="py-2 pr-4">
                    <div className="flex flex-wrap gap-1">
                      {k.allowed_providers.map((p) => (
                        <span
                          key={p}
                          className="rounded bg-fill px-1.5 py-0.5 text-[11px] text-fg-muted"
                        >
                          {p}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="py-2 pr-4 text-center">
                    {k.revoked_at ? (
                      <span className="text-xs text-fg-subtle">—</span>
                    ) : (
                      <button
                        onClick={() =>
                          api
                            .updateKey(k.id, { allow_live: !k.allow_live })
                            .then(refresh)
                            .catch((e: Error) => setError(e.message))
                        }
                        disabled={!keyLiveReady(k) && !k.allow_live}
                        title={
                          k.allow_live
                            ? "Pause live calls for this key (runs simulated)"
                            : keyLiveReady(k)
                              ? "Resume live calls for this key"
                              : "No provider key for this key's providers — add one under Provider keys"
                        }
                        className={`rounded px-2 py-0.5 text-[11px] font-bold disabled:cursor-not-allowed disabled:opacity-50 ${
                          k.allow_live
                            ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400"
                            : "bg-fill text-fg-subtle"
                        }`}
                      >
                        {k.allow_live ? "LIVE" : keyLiveReady(k) ? "paused" : "no key"}
                      </button>
                    )}
                  </td>
                  <td className="py-2 pr-4 text-right tabular-nums">{num(k.requests)}</td>
                  <td className="py-2 pr-4 text-right tabular-nums">{usd(k.cost)}</td>
                  <td className="py-2 pr-4">
                    {k.monthly_budget_usd == null ? (
                      <span className="text-xs text-fg-subtle">unlimited</span>
                    ) : (
                      <div className="w-32">
                        <div className="flex justify-between text-[10px] text-fg-subtle">
                          <span>{usd(k.spend_period)}</span>
                          <span>
                            {usd(k.monthly_budget_usd)}
                            {k.budget_period === "custom" ? "" : `/${k.budget_period[0]}`}
                          </span>
                        </div>
                        <div className="mt-0.5 h-1.5 rounded bg-fill">
                          <div
                            className={`h-1.5 rounded ${
                              k.spend_period >= k.monthly_budget_usd
                                ? "bg-red-500"
                                : "bg-brand-500"
                            }`}
                            style={{
                              width: `${Math.min(
                                100,
                                k.monthly_budget_usd > 0
                                  ? (k.spend_period / k.monthly_budget_usd) * 100
                                  : 100,
                              )}%`,
                            }}
                          />
                        </div>
                      </div>
                    )}
                  </td>
                  <td className="py-2 pr-4 text-xs text-fg-subtle">{relTime(k.last_used_at)}</td>
                  <td className="py-2 pr-0 text-right">
                    {k.revoked_at ? (
                      <span className="text-xs">revoked</span>
                    ) : (
                      <button
                        onClick={() =>
                          api
                            .revokeKey(k.id)
                            .then(refresh)
                            .catch((e: Error) => setError(e.message))
                        }
                        className="rounded border border-red-200 px-2 py-1 text-xs text-red-600 hover:bg-red-50 dark:border-red-500/30 dark:text-red-400 dark:hover:bg-red-500/10"
                      >
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
