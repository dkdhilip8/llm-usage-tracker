import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TimeseriesPoint } from "../lib/api";
import { Card } from "./Card";
import { usd } from "../lib/format";
import { chartTheme, useTheme } from "../lib/theme";

const COLORS: Record<string, string> = {
  openai: "#4f46e5",
  anthropic: "#0ea5e9",
  openrouter: "#f97316",
};

type Metric = "cost" | "total_tokens";

export function UsageOverTimeChart({ data }: { data: TimeseriesPoint[] }) {
  const [metric, setMetric] = useState<Metric>("cost");
  const ct = chartTheme(useTheme().isDark);

  const { rows, providers } = useMemo(() => {
    const providerSet = new Set<string>();
    const byDay = new Map<string, Record<string, string | number>>();
    for (const p of data) {
      providerSet.add(p.provider);
      const row = byDay.get(p.day) ?? { day: p.day };
      row[p.provider] = ((row[p.provider] as number) ?? 0) + p[metric];
      byDay.set(p.day, row);
    }
    const sorted = [...byDay.values()].sort((a, b) =>
      String(a.day).localeCompare(String(b.day)),
    );
    return { rows: sorted, providers: [...providerSet] };
  }, [data, metric]);

  return (
    <Card
      title="Usage over time"
      right={
        <div className="flex overflow-hidden rounded-md border border-line text-xs">
          {(["cost", "total_tokens"] as Metric[]).map((m) => (
            <button
              key={m}
              onClick={() => setMetric(m)}
              className={`px-2 py-1 ${
                metric === m
                  ? "bg-brand-600 text-white"
                  : "bg-surface text-fg-muted hover:bg-fill"
              }`}
            >
              {m === "cost" ? "Cost" : "Tokens"}
            </button>
          ))}
        </div>
      }
    >
      <div className="h-72 w-full">
        {rows.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-fg-subtle">
            No usage in this range yet
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={ct.grid} />
              <XAxis dataKey="day" tick={{ fontSize: 11 }} stroke={ct.axis} />
              <YAxis
                tick={{ fontSize: 11 }}
                stroke={ct.axis}
                tickFormatter={(v: number) =>
                  metric === "cost" ? usd(v) : String(v)
                }
                width={60}
              />
              <Tooltip
                formatter={(v: number) =>
                  metric === "cost" ? usd(v) : v.toLocaleString()
                }
                contentStyle={ct.tooltip.contentStyle}
                labelStyle={ct.tooltip.labelStyle}
                itemStyle={ct.tooltip.itemStyle}
              />
              {providers.map((p) => (
                <Area
                  key={p}
                  type="monotone"
                  dataKey={p}
                  stackId="1"
                  stroke={COLORS[p] ?? "#64748b"}
                  fill={COLORS[p] ?? "#64748b"}
                  fillOpacity={0.2}
                  isAnimationActive={false}
                />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </Card>
  );
}
