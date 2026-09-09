import { useState } from "react";
import type { ByKeyRow } from "../lib/api";
import { Card } from "./Card";
import { num, relTime, tokens, usd } from "../lib/format";

type SortKey = "cost" | "total_tokens" | "requests";

export function KeyUsageTable({ rows }: { rows: ByKeyRow[] }) {
  const [sort, setSort] = useState<SortKey>("cost");
  const sorted = [...rows].sort((a, b) => b[sort] - a[sort]);

  const header = (key: SortKey, label: string) => (
    <button
      onClick={() => setSort(key)}
      className={`font-medium ${sort === key ? "text-brand-600" : "text-fg-muted hover:text-fg"}`}
    >
      {label}
    </button>
  );

  return (
    <Card title="Per-key usage">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-fg-subtle">
              <th className="py-2 pr-4">User / Label</th>
              <th className="py-2 pr-4">Key prefix</th>
              <th className="py-2 pr-4 text-right">{header("requests", "Requests")}</th>
              <th className="py-2 pr-4 text-right">{header("total_tokens", "Total tokens")}</th>
              <th className="py-2 pr-4 text-right">{header("cost", "Cost")}</th>
              <th className="py-2 pr-0 text-right">Last used</th>
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 && (
              <tr>
                <td colSpan={6} className="py-8 text-center text-fg-subtle">
                  No usage in this range yet
                </td>
              </tr>
            )}
            {sorted.map((r) => (
              <tr key={r.key_id} className="border-b border-line last:border-0">
                <td className="py-2 pr-4 font-medium text-fg">{r.label}</td>
                <td className="py-2 pr-4 font-mono text-xs text-fg-muted">
                  {r.key_prefix}…
                </td>
                <td className="py-2 pr-4 text-right tabular-nums">{num(r.requests)}</td>
                <td className="py-2 pr-4 text-right tabular-nums">{tokens(r.total_tokens)}</td>
                <td className="py-2 pr-4 text-right tabular-nums font-medium">
                  {usd(r.cost)}
                </td>
                <td className="py-2 pr-0 text-right text-xs text-fg-subtle">
                  {relTime(r.last_used_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
