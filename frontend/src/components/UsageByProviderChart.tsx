import { useMemo } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import type { ByModelRow } from "../lib/api";
import { Card } from "./Card";
import { tokens } from "../lib/format";
import { chartTheme, useTheme } from "../lib/theme";

const COLORS: Record<string, string> = {
  openai: "#4f46e5",
  anthropic: "#0ea5e9",
  openrouter: "#f97316",
};

export function UsageByProviderChart({ data }: { data: ByModelRow[] }) {
  const ct = chartTheme(useTheme().isDark);
  const slices = useMemo(() => {
    const byProvider = new Map<string, number>();
    for (const r of data) {
      byProvider.set(r.provider, (byProvider.get(r.provider) ?? 0) + r.total_tokens);
    }
    return [...byProvider.entries()].map(([provider, value]) => ({ provider, value }));
  }, [data]);

  const total = slices.reduce((a, s) => a + s.value, 0);

  return (
    <Card title="Usage by provider">
      <div className="h-72 w-full">
        {total === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-fg-subtle">
            No usage in this range yet
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={slices}
                dataKey="value"
                nameKey="provider"
                innerRadius={55}
                outerRadius={90}
                paddingAngle={2}
                label={(d: { provider: string }) => d.provider}
              >
                {slices.map((s) => (
                  <Cell key={s.provider} fill={COLORS[s.provider] ?? "#64748b"} />
                ))}
              </Pie>
              <Tooltip
                formatter={(v: number) => `${tokens(v)} tokens`}
                contentStyle={ct.tooltip.contentStyle}
                labelStyle={ct.tooltip.labelStyle}
                itemStyle={ct.tooltip.itemStyle}
              />
            </PieChart>
          </ResponsiveContainer>
        )}
      </div>
    </Card>
  );
}
