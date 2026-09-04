import type { UsageSummary } from "../lib/api";

function Cell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2">
      <div className="text-[10px] font-medium uppercase tracking-wide text-fg-subtle">
        {label}
      </div>
      <div className="mt-1 text-lg font-semibold tabular-nums text-fg">{value}</div>
    </div>
  );
}

export function MetricsRow({
  data,
  loading,
}: {
  data: UsageSummary | null;
  loading: boolean;
}) {
  if (loading || !data) {
    return (
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="h-[58px] animate-pulse rounded-lg border border-line bg-surface"
          />
        ))}
      </div>
    );
  }
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <Cell label="Latency p50" value={`${data.latency_p50_ms} ms`} />
      <Cell label="Latency p95" value={`${data.latency_p95_ms} ms`} />
      <Cell
        label="Error rate"
        value={`${(data.error_rate * 100).toFixed(1)}%`}
      />
      <Cell label="Tokens / sec" value={data.tokens_per_sec.toLocaleString()} />
    </div>
  );
}
