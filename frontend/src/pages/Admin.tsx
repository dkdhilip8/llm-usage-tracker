import { useCallback, useEffect, useState } from "react";
import {
  api,
  getAdminToken,
  PROVIDERS,
  setAdminToken,
  type KeyCreated,
  type KeyRow,
  type ProviderStatus,
} from "../lib/api";
import { Card } from "../components/Card";
import { num, relTime, usd } from "../lib/format";

function ProviderPanel() {
  const [rows, setRows] = useState<ProviderStatus[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback((refresh = false) => {
    setBusy(true);
    api
      .providers(refresh)
      .then((r) => {
        setRows(r);
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
      {err && <div className="text-sm text-red-600">{err}</div>}
      <ul className="space-y-2 text-sm">
        {(rows ?? []).map((p) => {
          const [dot, text] = p.configured
            ? p.valid
              ? ["bg-emerald-500", "configured & valid"]
              : ["bg-amber-500", "configured, check failed"]
            : ["bg-slate-300", "not configured"];
          return (
            <li key={p.provider} className="flex items-center gap-2">
              <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${dot}`} />
              <span className="font-medium capitalize">{p.provider}</span>
              <code className="rounded bg-slate-100 px-1 text-[11px] text-slate-500">
                {p.env_var}
              </code>
              <span className="ml-auto text-xs text-slate-400">{text}</span>
            </li>
          );
        })}
      </ul>

      <details className="mt-3 text-[11px] text-slate-500">
        <summary className="cursor-pointer text-brand-600">How to configure</summary>
        <div className="mt-2 space-y-1">
          <p>
            Set the provider key as a <strong>server environment variable</strong> — never entered
            here, never stored in the database.
          </p>
          <p>
            Local: add to <code>backend/.env</code> or the <code>backend</code> service in{" "}
            <code>docker-compose.yml</code>, then restart.
          </p>
          <p>
            Render: service → <em>Environment</em> → add <code>OPENAI_API_KEY</code> etc., then{" "}
            <code>ENABLE_LIVE=true</code>.
          </p>
          <p>
            A request only goes live when <code>ENABLE_LIVE=true</code> <em>and</em> the virtual key
            has <em>allow live</em> <em>and</em> the provider is configured &amp; valid — otherwise
            it stays simulated.
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
      });
      setCreated(res);
      setCopied(false);
      setLabel("");
      setBudget("");
      onCreated();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  return (
    <Card title="Create a virtual key">
      <div className="space-y-3">
        <input
          className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          placeholder="Label — e.g. Jane (Data Science) or Support Bot"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />

        <div>
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
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

        <div className="grid grid-cols-2 gap-2">
          <label className="text-xs font-medium text-slate-500">
            Default provider
            <select
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
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
          <label className="text-xs font-medium text-slate-500">
            Monthly budget (USD)
            <input
              type="number"
              step="0.01"
              min="0"
              placeholder="unlimited"
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
            />
          </label>
        </div>

        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={allowLive}
            onChange={(e) => setAllowLive(e.target.checked)}
          />
          Allow live calls
          <span className="text-xs text-slate-400">
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

        {err && <div className="text-sm text-red-600">{err}</div>}

        {created && (
          <div className="rounded-md border border-emerald-200 bg-emerald-50 p-3">
            <div className="text-xs font-medium text-emerald-800">
              Copy this key now — it is shown only once.
            </div>
            <div className="mt-1 flex items-center gap-2">
              <code className="flex-1 break-all rounded bg-white px-2 py-1 font-mono text-xs text-slate-700">
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
            <div className="mt-1 text-xs text-emerald-700">
              {created.label} · {created.allowed_providers.join(", ")}
              {created.allow_live ? " · live-allowed" : ""}
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

export function Admin() {
  const [token, setToken] = useState(getAdminToken());
  const [savedToken, setSavedToken] = useState(getAdminToken());
  const [keys, setKeys] = useState<KeyRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    if (!savedToken) return;
    api
      .listKeys()
      .then(setKeys)
      .catch((e: Error) => setError(e.message));
  }, [savedToken]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (!savedToken) {
    return (
      <div className="mx-auto max-w-md">
        <Card title="Admin access">
          <p className="mb-3 text-sm text-slate-500">
            Enter the admin token to manage providers and virtual keys. Viewing the
            dashboard needs no token.
          </p>
          <input
            type="password"
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            placeholder="ADMIN_TOKEN"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
          <button
            onClick={() => {
              setAdminToken(token.trim());
              setSavedToken(token.trim());
              setError(null);
            }}
            disabled={!token.trim()}
            className="mt-3 w-full rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
          >
            Continue
          </button>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-slate-800">Gateway admin</h1>
        <button
          onClick={() => {
            setAdminToken("");
            setSavedToken("");
            setKeys([]);
          }}
          className="text-sm text-slate-500 hover:underline"
        >
          Sign out of admin
        </button>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <ProviderPanel />
        <CreateKeyForm onCreated={refresh} />
      </div>

      <Card title={`Virtual keys (${keys.length})`}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-left text-xs uppercase tracking-wide text-slate-400">
                <th className="py-2 pr-4">Label</th>
                <th className="py-2 pr-4">Prefix</th>
                <th className="py-2 pr-4">Allowed providers</th>
                <th className="py-2 pr-4 text-center">Live</th>
                <th className="py-2 pr-4 text-right">Requests</th>
                <th className="py-2 pr-4 text-right">Cost</th>
                <th className="py-2 pr-4">Budget (mo)</th>
                <th className="py-2 pr-4">Last used</th>
                <th className="py-2 pr-0 text-right">Status</th>
              </tr>
            </thead>
            <tbody>
              {keys.length === 0 && (
                <tr>
                  <td colSpan={9} className="py-8 text-center text-slate-400">
                    No keys yet — create one above.
                  </td>
                </tr>
              )}
              {keys.map((k) => (
                <tr
                  key={k.id}
                  className={`border-b border-slate-50 last:border-0 ${
                    k.revoked_at ? "text-slate-400" : ""
                  }`}
                >
                  <td className="py-2 pr-4 font-medium">{k.label}</td>
                  <td className="py-2 pr-4 font-mono text-xs">{k.key_prefix}…</td>
                  <td className="py-2 pr-4">
                    <div className="flex flex-wrap gap-1">
                      {k.allowed_providers.map((p) => (
                        <span
                          key={p}
                          className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600"
                        >
                          {p}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="py-2 pr-4 text-center">
                    {k.revoked_at ? (
                      <span className="text-xs text-slate-300">—</span>
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
                            ? "bg-emerald-100 text-emerald-700"
                            : "bg-slate-100 text-slate-400"
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
                      <span className="text-xs text-slate-300">unlimited</span>
                    ) : (
                      <div className="w-28">
                        <div className="flex justify-between text-[10px] text-slate-400">
                          <span>{usd(k.spend_month)}</span>
                          <span>{usd(k.monthly_budget_usd)}</span>
                        </div>
                        <div className="mt-0.5 h-1.5 rounded bg-slate-100">
                          <div
                            className={`h-1.5 rounded ${
                              k.spend_month >= k.monthly_budget_usd
                                ? "bg-red-500"
                                : "bg-brand-500"
                            }`}
                            style={{
                              width: `${Math.min(
                                100,
                                k.monthly_budget_usd > 0
                                  ? (k.spend_month / k.monthly_budget_usd) * 100
                                  : 100,
                              )}%`,
                            }}
                          />
                        </div>
                      </div>
                    )}
                  </td>
                  <td className="py-2 pr-4 text-xs text-slate-400">
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
                        className="rounded border border-red-200 px-2 py-1 text-xs text-red-600 hover:bg-red-50"
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
