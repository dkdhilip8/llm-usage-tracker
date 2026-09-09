import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  api,
  PROVIDERS,
  type KeyRow,
  type ModelInfo,
  type RequestRow,
} from "../lib/api";
import { Card } from "../components/Card";
import { relTime, usd } from "../lib/format";

const FILTER_KEYS = ["key_id", "provider", "model", "status", "start", "end"] as const;

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-wide text-fg-subtle">{label}</span>
      {children}
    </label>
  );
}

export function Requests() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState<RequestRow[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [more, setMore] = useState(false);
  const [bodiesLogged, setBodiesLogged] = useState(true);
  const [open, setOpen] = useState<RequestRow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [keys, setKeys] = useState<KeyRow[]>([]);

  useEffect(() => {
    api.models().then(setModels).catch(() => setModels([]));
    api.listKeys().then(setKeys).catch(() => setKeys([]));
  }, []);

  const activeFilters: [string, string][] = FILTER_KEYS.flatMap((k) => {
    const v = params.get(k);
    return v != null ? [[k, v] as [string, string]] : [];
  });
  const filterKey = params.toString();
  const hasFilters = activeFilters.length > 0;

  const setFilter = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(params);
      if (value) next.set(key, value);
      else next.delete(key);
      if (key === "provider") next.delete("model"); // model list depends on provider
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  const modelOptions = useMemo(() => {
    const p = params.get("provider");
    const list = p ? models.filter((m) => m.provider === p) : models;
    return list.map((m) => m.model);
  }, [models, params]);

  const load = useCallback(
    (after: number | null) => {
      const p = new URLSearchParams(params);
      p.set("limit", "50");
      if (after) p.set("cursor", String(after));
      api
        .listRequests("?" + p.toString())
        .then((r) => {
          setRows((prev) => (after ? [...prev, ...r.items] : r.items));
          setCursor(r.next_cursor);
          setMore(r.next_cursor != null);
          setBodiesLogged(r.bodies_logged);
          setError(null);
        })
        .catch((e: Error) => setError(e.message));
    },
    [params],
  );

  useEffect(() => {
    load(null);
  }, [load, filterKey]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-fg">Request log</h1>
        <button
          onClick={() => load(null)}
          className="rounded-md border border-line bg-surface px-3 py-1.5 text-sm text-fg-muted hover:bg-fill"
        >
          Refresh
        </button>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 dark:border-red-500/30 bg-red-50 dark:bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-400">
          {error}
        </div>
      )}

      <div className="flex flex-wrap items-end gap-2 rounded-md border border-line bg-surface-2 px-3 py-2 text-xs">
        <Field label="Key">
          <select
            className="rounded border border-line bg-surface px-2 py-1 text-xs"
            value={params.get("key_id") ?? ""}
            onChange={(e) => setFilter("key_id", e.target.value)}
          >
            <option value="">All keys</option>
            {keys.map((k) => (
              <option key={k.id} value={k.id}>
                {k.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Provider">
          <select
            className="rounded border border-line bg-surface px-2 py-1 text-xs capitalize"
            value={params.get("provider") ?? ""}
            onChange={(e) => setFilter("provider", e.target.value)}
          >
            <option value="">All</option>
            {PROVIDERS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Model">
          <select
            className="rounded border border-line bg-surface px-2 py-1 text-xs"
            value={params.get("model") ?? ""}
            onChange={(e) => setFilter("model", e.target.value)}
          >
            <option value="">All</option>
            {modelOptions.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Status">
          <select
            className="rounded border border-line bg-surface px-2 py-1 text-xs"
            value={params.get("status") ?? ""}
            onChange={(e) => setFilter("status", e.target.value)}
          >
            <option value="">All</option>
            <option value="success">Success</option>
            <option value="error">Error</option>
          </select>
        </Field>
        <Field label="From">
          <input
            type="date"
            className="rounded border border-line bg-surface px-2 py-1 text-xs"
            value={(params.get("start") ?? "").slice(0, 10)}
            onChange={(e) => setFilter("start", e.target.value)}
          />
        </Field>
        <Field label="To">
          <input
            type="date"
            className="rounded border border-line bg-surface px-2 py-1 text-xs"
            value={(params.get("end") ?? "").slice(0, 10)}
            onChange={(e) => setFilter("end", e.target.value)}
          />
        </Field>
        {hasFilters && (
          <button
            onClick={() => navigate("/requests")}
            className="ml-auto self-center text-brand-600 hover:underline"
          >
            Clear all
          </button>
        )}
      </div>
      {!bodiesLogged && (
        <div className="rounded-md border border-line bg-surface-2 px-3 py-2 text-xs text-fg-muted">
          Prompt / response bodies are not stored (set <code>LOG_BODIES=true</code> to capture
          truncated previews).
        </div>
      )}

      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-fg-subtle">
                <th className="py-2 pr-3">When</th>
                <th className="py-2 pr-3">Key</th>
                <th className="py-2 pr-3">Model</th>
                <th className="py-2 pr-3 text-right">Tokens</th>
                <th className="py-2 pr-3 text-right">Cost</th>
                <th className="py-2 pr-3 text-right">Latency</th>
                <th className="py-2 pr-0 text-center">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-8 text-center text-fg-subtle">
                    No requests yet.
                  </td>
                </tr>
              )}
              {rows.map((r) => (
                <tr
                  key={r.id}
                  onClick={() => setOpen(r)}
                  className="cursor-pointer border-b border-line last:border-0 hover:bg-fill"
                >
                  <td className="py-2 pr-3 text-xs text-fg-subtle">{relTime(r.ts)}</td>
                  <td className="py-2 pr-3">{r.key_label}</td>
                  <td className="py-2 pr-3 font-mono text-xs">
                    {r.provider}/{r.model}
                  </td>
                  <td className="py-2 pr-3 text-right tabular-nums">{r.total_tokens}</td>
                  <td className="py-2 pr-3 text-right tabular-nums">
                    {usd(r.cost)}
                    <span className="ml-1 text-[10px] text-fg-subtle">
                      {r.cost_source === "provider" ? "actual" : "est"}
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-right tabular-nums">{r.latency_ms} ms</td>
                  <td className="py-2 pr-0 text-center">
                    <span
                      className={
                        r.status === "error" ? "text-red-600 dark:text-red-400" : "text-fg-subtle"
                      }
                    >
                      {r.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {more && (
          <button
            onClick={() => load(cursor)}
            className="mt-3 w-full rounded-md border border-line py-1.5 text-sm text-brand-600 hover:bg-fill"
          >
            Load more
          </button>
        )}
      </Card>

      {open && (
        <div
          className="fixed inset-0 z-20 flex justify-end bg-black/20"
          onClick={() => setOpen(null)}
        >
          <div
            className="h-full w-full max-w-lg overflow-y-auto bg-surface p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-semibold text-fg">Request detail</h2>
              <button
                onClick={() => setOpen(null)}
                className="text-fg-subtle hover:text-fg"
              >
                ✕
              </button>
            </div>
            <dl className="space-y-1.5 text-sm">
              {[
                ["Request ID", open.request_id],
                ["Time", new Date(open.ts).toLocaleString()],
                ["Key", `${open.key_label} (${open.key_prefix}…)`],
                ["Provider / model", `${open.provider}/${open.model}`],
                [
                  "Tokens",
                  `${open.prompt_tokens} prompt · ${open.completion_tokens} completion · ${open.total_tokens} total`,
                ],
                [
                  "Cost",
                  `${usd(open.cost)} (${open.cost_source === "provider" ? "provider actual" : "estimated"})`,
                ],
                ["Latency", `${open.latency_ms} ms`],
                ["Status", open.status],
              ].map(([k, v]) => (
                <div key={k} className="flex gap-2">
                  <dt className="w-32 shrink-0 text-fg-subtle">{k}</dt>
                  <dd className="font-mono text-xs text-fg">{v}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-4 space-y-3">
              <div>
                <div className="text-xs font-medium text-fg-subtle">Prompt</div>
                <pre className="mt-1 whitespace-pre-wrap rounded bg-surface-2 p-2 text-xs text-fg">
                  {open.prompt_preview ?? "— not stored —"}
                </pre>
              </div>
              <div>
                <div className="text-xs font-medium text-fg-subtle">Response</div>
                <pre className="mt-1 whitespace-pre-wrap rounded bg-surface-2 p-2 text-xs text-fg">
                  {open.response_preview ?? "— not stored —"}
                </pre>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
