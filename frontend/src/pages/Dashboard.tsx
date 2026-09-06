import { useCallback, useEffect, useState } from "react";
import {
  api,
  type ByKeyRow,
  type ByModelRow,
  type ModelInfo,
  type TimeseriesPoint,
  type UsageSummary,
} from "../lib/api";
import { useAuth } from "../lib/auth";
import { defaultFilters, toQuery, type Filters } from "../lib/filters";
import { FilterBar } from "../components/FilterBar";
import { SummaryCards } from "../components/SummaryCards";
import { MetricsRow } from "../components/MetricsRow";
import { UsageOverTimeChart } from "../components/UsageOverTimeChart";
import { UsageByProviderChart } from "../components/UsageByProviderChart";
import { UsageByModelChart } from "../components/UsageByModelChart";
import { KeyUsageTable } from "../components/KeyUsageTable";

export function Dashboard() {
  const { isAdmin } = useAuth();
  const [filters, setFilters] = useState<Filters>(defaultFilters);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [timeseries, setTimeseries] = useState<TimeseriesPoint[]>([]);
  const [byKey, setByKey] = useState<ByKeyRow[]>([]);
  const [byModel, setByModel] = useState<ByModelRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.models().then(setModels).catch(() => setModels([]));
  }, []);

  const load = useCallback(() => {
    const qs = toQuery(filters);
    setLoading(true);
    setError(null);
    Promise.all([
      api.usageSummary(qs),
      api.usageTimeseries(qs),
      api.usageByKey(qs),
      api.usageByModel(qs),
    ])
      .then(([s, ts, bk, bm]) => {
        setSummary(s);
        setTimeseries(ts);
        setByKey(bk);
        setByModel(bm);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [filters]);

  useEffect(() => {
    load();
  }, [load]);

  // key list for the filter dropdown — unfiltered so all keys are always selectable
  const [allKeys, setAllKeys] = useState<ByKeyRow[]>([]);
  useEffect(() => {
    api.usageByKey("").then(setAllKeys).catch(() => setAllKeys([]));
  }, []);

  const scopeLabel = isAdmin ? "all accounts" : "your account";

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-fg">
            {isAdmin ? "Organization LLM usage" : "LLM usage"}
          </h1>
          <div className="text-xs text-fg-subtle">Showing: {scopeLabel}</div>
        </div>
        <button
          onClick={load}
          className="rounded-md border border-line bg-surface px-3 py-1.5 text-sm text-fg-muted hover:bg-fill"
        >
          Refresh
        </button>
      </div>

      <FilterBar filters={filters} onChange={setFilters} models={models} keys={allKeys} />

      {error && (
        <div className="rounded-md border border-red-200 dark:border-red-500/30 bg-red-50 dark:bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-400">
          {error}
        </div>
      )}

      <SummaryCards data={summary} loading={loading} />
      <MetricsRow data={summary} loading={loading} />

      <div className="grid gap-4 lg:grid-cols-2">
        <UsageOverTimeChart data={timeseries} />
        <UsageByProviderChart data={byModel} />
      </div>

      <UsageByModelChart data={byModel} />

      <KeyUsageTable rows={byKey} />
    </div>
  );
}
