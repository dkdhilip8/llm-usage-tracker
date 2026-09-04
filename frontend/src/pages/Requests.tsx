import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, getAdminToken, type RequestRow } from "../lib/api";
import { Card } from "../components/Card";
import { relTime, usd } from "../lib/format";

const FILTER_KEYS = ["key_id", "provider", "model", "status", "mode", "start", "end"] as const;

export function Requests() {
  const hasToken = Boolean(getAdminToken());
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [rows, setRows] = useState<RequestRow[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [more, setMore] = useState(false);
  const [bodiesLogged, setBodiesLogged] = useState(true);
  const [open, setOpen] = useState<RequestRow | null>(null);
  const [error, setError] = useState<string | null>(null);

  const activeFilters: [string, string][] = FILTER_KEYS.flatMap((k) => {
    const v = params.get(k);
    return v != null ? [[k, v] as [string, string]] : [];
  });
  const filterKey = params.toString();

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
    if (hasToken) load(null);
  }, [hasToken, load, filterKey]);

  if (!hasToken) {
    return (
      <div className="mx-auto max-w-md">
        <Card title="Requests">
          <p className="text-sm text-fg-muted">
            Enter the admin token on the <strong>Admin</strong> page to view the request log.
          </p>
        </Card>
      </div>
    );
  }

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
      {activeFilters.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-line bg-surface-2 px-3 py-2 text-xs">
          <span className="text-fg-subtle">Filtered:</span>
          {activeFilters.map(([k, v]) => (
            <span
              key={k}
              className="rounded bg-fill px-1.5 py-0.5 font-mono text-[11px] text-fg-muted"
            >
              {k === "start" || k === "end"
                ? `${k}=${new Date(v).toLocaleString()}`
                : `${k}=${v}`}
            </span>
          ))}
          <button
            onClick={() => navigate("/requests")}
            className="ml-auto text-brand-600 hover:underline"
          >
            Clear
          </button>
        </div>
      )}
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
                <th className="py-2 pr-3 text-center">Mode</th>
                <th className="py-2 pr-3 text-right">Tokens</th>
                <th className="py-2 pr-3 text-right">Cost</th>
                <th className="py-2 pr-3 text-right">Latency</th>
                <th className="py-2 pr-0 text-center">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={8} className="py-8 text-center text-fg-subtle">
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
                  <td className="py-2 pr-3 text-center">
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                        r.mode === "live"
                          ? "bg-emerald-100 dark:bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
                          : "bg-amber-100 dark:bg-amber-500/15 text-amber-700 dark:text-amber-400"
                      }`}
                    >
                      {r.mode}
                    </span>
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
                ["Mode", open.mode],
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
