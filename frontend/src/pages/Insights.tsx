import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  api,
  getAdminToken,
  type InsightAlert,
  type Investigation,
} from "../lib/api";
import { Card } from "../components/Card";
import { usd } from "../lib/format";

function fmtValue(metric: "cost" | "tokens", n: number): string {
  return metric === "cost" ? usd(n) : `${Math.round(n).toLocaleString()} tokens`;
}

const MEDALS = ["🥇", "🥈", "🥉"];

export function Insights() {
  const hasToken = Boolean(getAdminToken());
  const navigate = useNavigate();
  const [alerts, setAlerts] = useState<InsightAlert[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Investigation | null>(null);
  const [analyzing, setAnalyzing] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .insights()
      .then((r) => {
        setAlerts(r.alerts);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (hasToken) load();
  }, [hasToken, load]);

  async function investigate(alert: InsightAlert) {
    setAnalyzing(alert.id);
    // brief pause so the "analyzing" step reads as a real action
    await new Promise((r) => setTimeout(r, 600));
    try {
      setSelected(await api.investigate(alert.id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAnalyzing(null);
    }
  }

  if (!hasToken) {
    return (
      <div className="mx-auto max-w-md">
        <Card title="Insights">
          <p className="text-sm text-fg-muted">
            Enter the admin token on the <strong>Admin</strong> page to view usage insights.
          </p>
        </Card>
      </div>
    );
  }

  if (selected) {
    const h = selected.headline;
    const noun = selected.metric === "cost" ? "COST" : "TOKEN";
    return (
      <div className="mx-auto max-w-2xl space-y-4">
        <button
          onClick={() => setSelected(null)}
          className="text-sm text-brand-600 hover:underline"
        >
          ← back to alerts
        </button>

        <Card>
          <div className="space-y-5">
            <div>
              <div className="text-xs font-semibold uppercase tracking-widest text-fg-subtle">
                {noun} investigation
              </div>
              <div className="mt-2 text-2xl font-semibold text-fg">
                {selected.metric === "cost" ? "Cost" : "Token usage"} increased by{" "}
                {h.pct_change}%
              </div>
              <div className="mt-1 text-lg text-fg-muted">
                {fmtValue(selected.metric, h.baseline)}{" "}
                <span className="text-fg-subtle">→</span>{" "}
                <span className="font-semibold text-fg">
                  {fmtValue(selected.metric, h.current)}
                </span>
              </div>
              <div className="mt-1 text-[11px] text-fg-subtle">
                Analyzed: {selected.analyzed.join(" · ")}
              </div>
            </div>

            <div>
              <div className="mb-2 text-xs font-semibold uppercase tracking-widest text-fg-subtle">
                Primary contributors
              </div>
              <div className="space-y-2">
                {selected.contributors.map((c, i) => (
                  <div key={c.label}>
                    <div className="flex items-baseline justify-between text-sm">
                      <span className="text-fg">
                        {MEDALS[i] ?? "•"} {c.label}
                        <span className="ml-2 text-xs text-fg-subtle">{c.detail}</span>
                      </span>
                      <span className="font-semibold tabular-nums text-fg">{c.pct}%</span>
                    </div>
                    <div className="mt-1 h-1.5 rounded bg-fill">
                      <div
                        className="h-1.5 rounded bg-brand-500"
                        style={{ width: `${Math.min(Math.max(c.pct, 0), 100)}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div>
              <div className="mb-1 text-xs font-semibold uppercase tracking-widest text-fg-subtle">
                Analysis
              </div>
              <p className="rounded-md border border-line bg-surface-2 p-3 text-sm text-fg-muted">
                {selected.summary}
              </p>
            </div>

            <button
              onClick={() =>
                navigate(
                  "/requests?" +
                    new URLSearchParams(
                      Object.entries(selected.related_query).map(([k, v]) => [k, String(v)]),
                    ),
                )
              }
              className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
            >
              View related requests
            </button>
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-fg">Insights</h1>
        <button
          onClick={load}
          className="rounded-md border border-line bg-surface px-3 py-1.5 text-sm text-fg-muted hover:bg-fill"
        >
          Refresh
        </button>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-400">
          {error}
        </div>
      )}

      {alerts && alerts.length === 0 && (
        <Card>
          <p className="text-sm text-fg-muted">
            No anomalies in the last 24 hours. Generate some via{" "}
            <strong>Admin → Demo tools → Inject usage spike</strong>, or send bursts of requests
            from the Playground.
          </p>
        </Card>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        {(alerts ?? []).map((a) => (
          <Card key={a.id}>
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <span
                  className={`text-sm ${
                    a.severity === "critical" ? "text-red-600 dark:text-red-400" : "text-amber-600 dark:text-amber-400"
                  }`}
                >
                  {a.severity === "critical" ? "🔴" : "⚠️"}
                </span>
                <span className="font-semibold text-fg">{a.title}</span>
                <span
                  className={`ml-auto rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                    a.severity === "critical"
                      ? "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-400"
                      : "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400"
                  }`}
                >
                  {a.severity}
                </span>
              </div>
              <p className="text-sm text-fg-muted">{a.detail}</p>
              <div className="text-xs text-fg-subtle">
                {fmtValue(a.metric, a.baseline)} → {fmtValue(a.metric, a.current)}{" "}
                <span className="font-semibold text-fg-muted">(+{a.pct_change}%)</span>
              </div>
              <button
                onClick={() => investigate(a)}
                disabled={analyzing === a.id}
                className="mt-1 rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-60"
              >
                {analyzing === a.id ? "Analyzing…" : "Investigate"}
              </button>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
