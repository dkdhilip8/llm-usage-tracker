import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ByModelRow } from "../lib/api";
import { Card } from "./Card";
import { tokens } from "../lib/format";

export function UsageByModelChart({ data }: { data: ByModelRow[] }) {
  const rows = [...data]
    .sort((a, b) => b.total_tokens - a.total_tokens)
    .map((r) => ({ name: r.model, tokens: r.total_tokens, provider: r.provider }));

  return (
    <Card title="Tokens by model">
      <div className="h-72 w-full">
        {rows.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-slate-400">
            No usage in this range yet
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={rows}
              layout="vertical"
              margin={{ top: 4, right: 16, bottom: 4, left: 8 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" horizontal={false} />
              <XAxis
                type="number"
                tick={{ fontSize: 11 }}
                stroke="#94a3b8"
                tickFormatter={(v: number) => tokens(v)}
              />
              <YAxis
                type="category"
                dataKey="name"
                tick={{ fontSize: 11 }}
                stroke="#94a3b8"
                width={200}
              />
              <Tooltip formatter={(v: number) => `${v.toLocaleString()} tokens`} />
              <Bar dataKey="tokens" fill="#4f46e5" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </Card>
  );
}
