import type { UsageSummary } from "../lib/api";
import { num, tokens, usd } from "../lib/format";

function Stat({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className="mt-2 text-2xl font-semibold text-slate-900">{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-400">{sub}</div>}
    </div>
  );
}

export function SummaryCards({
  data,
  loading,
}: {
  data: UsageSummary | null;
  loading: boolean;
}) {
  if (loading || !data) {
    return (
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="h-[92px] animate-pulse rounded-xl border border-slate-200 bg-white"
          />
        ))}
      </div>
    );
  }
  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <Stat label="Total Requests" value={num(data.total_requests)} />
      <Stat
        label="Total Tokens"
        value={tokens(data.total_tokens)}
        sub={`${num(data.total_prompt_tokens)} in · ${num(data.total_completion_tokens)} out`}
      />
      <Stat
        label="Cost"
        value={usd(data.total_cost)}
        sub={
          data.cost_actual > 0
            ? `${usd(data.cost_actual)} actual · ${usd(data.cost_estimated)} est.`
            : "estimated from configured pricing"
        }
      />
      <Stat label="Active Virtual Keys" value={num(data.active_keys)} />
    </div>
  );
}
