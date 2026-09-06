import { useCallback, useEffect, useState } from "react";
import {
  api,
  PROVIDERS,
  type BudgetPeriod,
  type KeyCreated,
  type KeyRow,
  type ProviderStatus,
} from "../lib/api";
import { useAuth } from "../lib/auth";
import { Card } from "../components/Card";
import { num, relTime, usd } from "../lib/format";

function ProviderKeyRow({
  p,
  onChange,
}: {
  p: ProviderStatus;
  onChange: () => void;
}) {
  const [editing, setEditing] = useState(p.source === "none");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      await api.setProviderKey(p.provider, value.trim());
      setValue("");
      setEditing(false);
      onChange();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function clear() {
    setBusy(true);
    setErr(null);
    try {
      await api.clearProviderKey(p.provider);
      onChange();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (p.source === "env") {
    return (
      <span className="ml-auto rounded bg-fill px-1.5 py-0.5 text-[10px] text-fg-muted">
        set via server env
      </span>
    );
  }
  return (
    <div className="ml-auto flex items-center gap-1.5">
      {p.source === "db" && !editing && (
        <>
          <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400">
            saved ····{p.last4}
          </span>
          <button
            onClick={() => setEditing(true)}
            className="text-[11px] text-brand-600 hover:underline"
          >
            replace
          </button>
          <button
            onClick={clear}
            disabled={busy}
            className="text-[11px] text-red-600 hover:underline disabled:opacity-50"
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
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          <button
            onClick={save}
            disabled={busy || value.trim().length < 8}
            className="rounded bg-brand-600 px-1.5 py-0.5 text-[10px] font-medium text-white hover:bg-brand-700 disabled:opacity-50"
          >
            save
          </button>
          {p.source === "db" && (
            <button
              onClick={() => setEditing(false)}
              className="text-[11px] text-fg-subtle hover:underline"
            >
              cancel
            </button>
          )}
        </>
      )}
      {err && <span className="text-[10px] text-red-600">{err}</span>}
    </div>
  );
}

function ProviderPanel() {
  const [rows, setRows] = useState<ProviderStatus[] | null>(null);
  const [dbKeys, setDbKeys] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback((refresh = false) => {
    setBusy(true);
    Promise.all([api.providers(refresh), api.health()])
      .then(([r, h]) => {
        setRows(r);
        setDbKeys(h.db_keys_enabled);
        setErr(null);
      })
      .catch((e: Error) => setErr(e.message))
      .finally(() => setBusy(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <Card
      title="Providers"
      right={
        <button
          onClick={() => load(true)}
          disabled={busy}
          className="text-xs text-brand-600 hover:underline disabled:opacity-50"
        >
          {busy ? "checking…" : "re-check"}
        </button>
      }
    >
      {err && <div className="text-sm text-red-600 dark:text-red-400">{err}</div>}
      <ul className="space-y-2 text-sm">
        {(rows ?? []).map((p) => {
          const [dot, text] = p.configured
            ? p.valid
              ? ["bg-emerald-500", "configured & valid"]
              : ["bg-amber-500", "configured, check failed"]
            : ["bg-fill", "not configured"];
          return (
            <li key={p.provider} className="flex items-center gap-2">
              <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${dot}`} />
              <span className="font-medium capitalize">{p.provider}</span>
              {dbKeys ? (
                <ProviderKeyRow p={p} onChange={() => load()} />
              ) : (
                <>
                  <code className="rounded bg-fill px-1 text-[11px] text-fg-muted">
                    {p.env_var}
                  </code>
                  <span className="ml-auto text-xs text-fg-subtle">{text}</span>
                </>
              )}
            </li>
          );
        })}
      </ul>

      <details className="mt-3 text-[11px] text-fg-muted">
        <summary className="cursor-pointer text-brand-600">How keys are resolved</summary>
        <div className="mt-2 space-y-1">
          <p>
            A <strong>server environment variable</strong> (<code>OPENAI_API_KEY</code> etc.)
            always wins.
          </p>
          {dbKeys ? (
            <p>
              Otherwise a key pasted here is stored <strong>AES-encrypted</strong> in the
              database (only its last 4 digits are ever shown back). This is a local /
              non-production convenience — the public deploy uses env vars only.
            </p>
          ) : (
            <p>
              DB-stored keys are <strong>off</strong> on this deployment. Set provider keys as
              server env vars (Render → <em>Environment</em>), then <code>ENABLE_LIVE=true</code>.
            </p>
          )}
          <p>
            A request only goes live when <code>ENABLE_LIVE=true</code> <em>and</em> the virtual
            key has <em>allow live</em> <em>and</em> the provider is configured &amp; valid.
          </p>
        </div>
      </details>
    </Card>
  );
}

function CreateKeyForm({ onCreated }: { onCreated: () => void }) {
  const [label, setLabel] = useState("");
  const [allowed, setAllowed] = useState<string[]>(["openai"]);
  const [allowLive, setAllowLive] = useState(false);
  const [defaultProvider, setDefaultProvider] = useState("");
  const [budget, setBudget] = useState("");
  const [budgetPeriod, setBudgetPeriod] = useState<BudgetPeriod>("month");
  const [budgetStart, setBudgetStart] = useState("");
  const [budgetEnd, setBudgetEnd] = useState("");
  const [created, setCreated] = useState<KeyCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function toggle(p: string) {
    setAllowed((a) => (a.includes(p) ? a.filter((x) => x !== p) : [...a, p]));
  }

  async function submit() {
    setErr(null);
    try {
      const res = await api.createKey({
        label: label.trim(),
        allowed_providers: allowed,
        allow_live: allowLive,
        default_provider: defaultProvider || null,
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
            {PROVIDERS.map((p) => (
              <label key={p} className="flex items-center gap-1.5 text-sm">
                <input
                  type="checkbox"
                  checked={allowed.includes(p)}
                  onChange={() => toggle(p)}
                />
                <span className="capitalize">{p}</span>
              </label>
            ))}
          </div>
        </div>

        <label className="block text-xs font-medium text-fg-muted">
          Default provider
          <select
            className="mt-1 w-full rounded-md border border-line px-2 py-1.5 text-sm"
            value={defaultProvider}
            onChange={(e) => setDefaultProvider(e.target.value)}
          >
            <option value="">none (require provider/ prefix)</option>
            {allowed.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>

        <div className="grid grid-cols-2 gap-2">
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
          <label className="text-xs font-medium text-fg-muted">
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
        </div>

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

        <label className="flex items-center gap-2 text-sm text-fg-muted">
          <input
            type="checkbox"
            checked={allowLive}
            onChange={(e) => setAllowLive(e.target.checked)}
          />
          Allow live calls
          <span className="text-xs text-fg-subtle">
            (still gated by <code>ENABLE_LIVE</code> + provider validity)
          </span>
        </label>

        <button
          onClick={submit}
          disabled={!label.trim() || allowed.length === 0}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
        >
          Create key
        </button>

        {err && <div className="text-sm text-red-600 dark:text-red-400">{err}</div>}

        {created && (
          <div className="rounded-md border border-emerald-200 dark:border-emerald-500/30 bg-emerald-50 dark:bg-emerald-500/10 p-3">
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

function DemoTools({ onChange }: { onChange: () => void }) {
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState<null | "spike" | "reset">(null);

  function run(kind: "spike" | "reset") {
    setBusy(kind);
    setMsg(null);
    const call =
      kind === "spike"
        ? api.injectDemoSpike().then((r) => `Inserted ${r.inserted} rows (${r.key_label}). Open Insights →`)
        : api.resetDemo().then((r) => `Rebuilt: ${r.keys} keys · ${r.usage_rows} rows · ${r.days} days.`);
    call
      .then((m) => {
        setMsg(m);
        onChange();
      })
      .catch((e: Error) => setMsg(e.message))
      .finally(() => setBusy(null));
  }

  return (
    <Card title="Demo tools">
      <div className="space-y-2">
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => run("spike")}
            disabled={busy !== null}
            className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {busy === "spike" ? "Injecting…" : "Inject usage spike"}
          </button>
          <button
            onClick={() => {
              if (
                window.confirm(
                  "Reset the demo dataset? This deletes ALL keys and usage, then rebuilds " +
                    "the shared ~30-day demo data.",
                )
              )
                run("reset");
            }}
            disabled={busy !== null}
            className="rounded-md border border-line px-4 py-2 text-sm font-medium text-fg-muted hover:bg-fill disabled:opacity-50"
          >
            {busy === "reset" ? "Rebuilding…" : "Reset demo dataset"}
          </button>
        </div>
        {msg && <div className="text-xs text-fg-muted">{msg}</div>}
        <p className="text-[11px] text-fg-subtle">
          <strong>Spike</strong> adds a last-24h anomaly on a demo key so Insights has something to
          show. <strong>Reset</strong> wipes everything and regenerates the deterministic shared
          dataset (5 keys, ~30 days of simulated usage, one built-in spike) — this is the data every
          public visitor sees.
        </p>
      </div>
    </Card>
  );
}

function LoginForm() {
  const { login } = useAuth();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await login(username.trim(), password);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-md">
      <Card title="Admin sign in">
        <p className="mb-3 text-sm text-fg-muted">
          Manage providers and virtual keys. The Dashboard, Requests and Insights tabs
          need no sign in.
        </p>
        <form onSubmit={submit} className="space-y-3">
          <label className="block text-xs font-medium text-fg-muted">
            Username
            <input
              className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>
          <label className="block text-xs font-medium text-fg-muted">
            Password
            <input
              type="password"
              autoComplete="current-password"
              className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {err && <div className="text-sm text-red-600 dark:text-red-400">{err}</div>}
          <button
            type="submit"
            disabled={busy || !username.trim() || !password}
            className="w-full rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </Card>
    </div>
  );
}

export function Admin() {
  const { authenticated, loading, logout } = useAuth();
  const [keys, setKeys] = useState<KeyRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    if (!authenticated) return;
    api
      .listKeys()
      .then(setKeys)
      .catch((e: Error) => setError(e.message));
  }, [authenticated]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (loading) {
    return <div className="mx-auto max-w-md p-6 text-sm text-fg-subtle">Loading…</div>;
  }
  if (!authenticated) {
    return <LoginForm />;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-fg">Gateway admin</h1>
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
        <div className="rounded-md border border-red-200 dark:border-red-500/30 bg-red-50 dark:bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-400">
          {error}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <ProviderPanel />
        <CreateKeyForm onCreated={refresh} />
      </div>

      <DemoTools onChange={refresh} />

      <Card title={`Virtual keys (${keys.length})`}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-fg-subtle">
                <th className="py-2 pr-4">Label</th>
                <th className="py-2 pr-4">Prefix</th>
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
                  <td colSpan={9} className="py-8 text-center text-fg-subtle">
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
                        title="Toggle live calls for this key"
                        className={`rounded px-2 py-0.5 text-[11px] font-bold ${
                          k.allow_live
                            ? "bg-emerald-100 dark:bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
                            : "bg-fill text-fg-subtle"
                        }`}
                      >
                        {k.allow_live ? "LIVE ✓" : "live ✗"}
                      </button>
                    )}
                  </td>
                  <td className="py-2 pr-4 text-right tabular-nums">{num(k.requests)}</td>
                  <td className="py-2 pr-4 text-right tabular-nums">
                    {usd(k.cost)}
                  </td>
                  <td className="py-2 pr-4">
                    {k.monthly_budget_usd == null ? (
                      <span className="text-xs text-fg-subtle">unlimited</span>
                    ) : (
                      <div className="w-32">
                        <div className="flex justify-between text-[10px] text-fg-subtle">
                          <span>{usd(k.spend_period)}</span>
                          <span>
                            {usd(k.monthly_budget_usd)}
                            {k.budget_period === "custom"
                              ? ""
                              : `/${k.budget_period[0]}`}
                          </span>
                        </div>
                        {k.budget_period === "custom" && k.budget_start && k.budget_end && (
                          <div className="text-[9px] text-fg-subtle">
                            {new Date(k.budget_start).toLocaleDateString()}–
                            {new Date(
                              new Date(k.budget_end).getTime() - 86400000,
                            ).toLocaleDateString()}
                          </div>
                        )}
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
                  <td className="py-2 pr-4 text-xs text-fg-subtle">
                    {relTime(k.last_used_at)}
                  </td>
                  <td className="py-2 pr-0 text-right">
                    {k.revoked_at ? (
                      <span className="text-xs">revoked</span>
                    ) : (
                      <button
                        onClick={() =>
                          api.revokeKey(k.id).then(refresh).catch((e: Error) =>
                            setError(e.message),
                          )
                        }
                        className="rounded border border-red-200 dark:border-red-500/30 px-2 py-1 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10"
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
