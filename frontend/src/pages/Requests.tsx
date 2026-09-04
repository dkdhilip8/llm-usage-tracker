import { useCallback, useEffect, useState } from "react";
import { api, getAdminToken, type RequestRow } from "../lib/api";
import { Card } from "../components/Card";
import { relTime, usd } from "../lib/format";

export function Requests() {
  const hasToken = Boolean(getAdminToken());
  const [rows, setRows] = useState<RequestRow[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [more, setMore] = useState(false);
  const [bodiesLogged, setBodiesLogged] = useState(true);
  const [open, setOpen] = useState<RequestRow | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((after: number | null) => {
    const qs = "?limit=50" + (after ? `&cursor=${after}` : "");
    api
      .listRequests(qs)
      .then((r) => {
        setRows((prev) => (after ? [...prev, ...r.items] : r.items));
        setCursor(r.next_cursor);
        setMore(r.next_cursor != null);
        setBodiesLogged(r.bodies_logged);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (hasToken) load(null);
  }, [hasToken, load]);

  if (!hasToken) {
    return (
      <div className="mx-auto max-w-md">
        <Card title="Requests">
          <p className="text-sm text-slate-500">
            Enter the admin token on the <strong>Admin</strong> page to view the request log.
          </p>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-slate-800">Request log</h1>
        <button
          onClick={() => load(null)}
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
        >
          Refresh
        </button>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}
      {!bodiesLogged && (
        <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500">
          Prompt / response bodies are not stored (set <code>LOG_BODIES=true</code> to capture
          truncated previews).
        </div>
      )}

      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-left text-xs uppercase tracking-wide text-slate-400">
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
                  <td colSpan={8} className="py-8 text-center text-slate-400">
                    No requests yet.
                  </td>
                </tr>
              )}
              {rows.map((r) => (
                <tr
                  key={r.id}
                  onClick={() => setOpen(r)}
                  className="cursor-pointer border-b border-slate-50 last:border-0 hover:bg-slate-50"
                >
                  <td className="py-2 pr-3 text-xs text-slate-400">{relTime(r.ts)}</td>
                  <td className="py-2 pr-3">{r.key_label}</td>
                  <td className="py-2 pr-3 font-mono text-xs">
                    {r.provider}/{r.model}
                  </td>
                  <td className="py-2 pr-3 text-center">
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                        r.mode === "live"
                          ? "bg-emerald-100 text-emerald-700"
                          : "bg-amber-100 text-amber-700"
                      }`}
                    >
                      {r.mode}
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-right tabular-nums">{r.total_tokens}</td>
                  <td className="py-2 pr-3 text-right tabular-nums">
                    {usd(r.cost)}
                    <span className="ml-1 text-[10px] text-slate-400">
                      {r.cost_source === "provider" ? "actual" : "est"}
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-right tabular-nums">{r.latency_ms} ms</td>
                  <td className="py-2 pr-0 text-center">
                    <span
                      className={
                        r.status === "error" ? "text-red-600" : "text-slate-400"
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
            className="mt-3 w-full rounded-md border border-slate-200 py-1.5 text-sm text-brand-600 hover:bg-slate-50"
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
            className="h-full w-full max-w-lg overflow-y-auto bg-white p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-semibold text-slate-800">Request detail</h2>
              <button
                onClick={() => setOpen(null)}
                className="text-slate-400 hover:text-slate-600"
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
                  <dt className="w-32 shrink-0 text-slate-400">{k}</dt>
                  <dd className="font-mono text-xs text-slate-700">{v}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-4 space-y-3">
              <div>
                <div className="text-xs font-medium text-slate-400">Prompt</div>
                <pre className="mt-1 whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs text-slate-700">
                  {open.prompt_preview ?? "— not stored —"}
                </pre>
              </div>
              <div>
                <div className="text-xs font-medium text-slate-400">Response</div>
                <pre className="mt-1 whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs text-slate-700">
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
