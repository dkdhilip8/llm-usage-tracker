import type { ByKeyRow, ModelInfo } from "../lib/api";
import { RANGE_OPTIONS, type Filters } from "../lib/filters";

const selectClass =
  "rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-700 focus:border-brand-500 focus:outline-none";

export function FilterBar({
  filters,
  onChange,
  models,
  keys,
}: {
  filters: Filters;
  onChange: (f: Filters) => void;
  models: ModelInfo[];
  keys: ByKeyRow[];
}) {
  const providers = Array.from(new Set(models.map((m) => m.provider)));
  const visibleModels = filters.provider
    ? models.filter((m) => m.provider === filters.provider)
    : models;

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
      <div className="flex overflow-hidden rounded-md border border-slate-300">
        {RANGE_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            onClick={() => onChange({ ...filters, rangeDays: opt.value })}
            className={`px-3 py-1.5 text-sm ${
              filters.rangeDays === opt.value
                ? "bg-brand-600 text-white"
                : "bg-white text-slate-600 hover:bg-slate-50"
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      <select
        className={selectClass}
        value={filters.provider}
        onChange={(e) =>
          onChange({ ...filters, provider: e.target.value, model: "" })
        }
      >
        <option value="">All providers</option>
        {providers.map((p) => (
          <option key={p} value={p}>
            {p}
          </option>
        ))}
      </select>

      <select
        className={selectClass}
        value={filters.model}
        onChange={(e) => onChange({ ...filters, model: e.target.value })}
      >
        <option value="">All models</option>
        {visibleModels.map((m) => (
          <option key={`${m.provider}/${m.model}`} value={m.model}>
            {m.model}
          </option>
        ))}
      </select>

      <select
        className={selectClass}
        value={filters.keyId}
        onChange={(e) => onChange({ ...filters, keyId: e.target.value })}
      >
        <option value="">All keys</option>
        {keys.map((k) => (
          <option key={k.key_id} value={String(k.key_id)}>
            {k.label}
          </option>
        ))}
      </select>

      {(filters.provider || filters.model || filters.keyId || filters.rangeDays !== 14) && (
        <button
          onClick={() =>
            onChange({ rangeDays: 14, provider: "", model: "", keyId: "" })
          }
          className="ml-auto text-sm text-brand-600 hover:underline"
        >
          Reset
        </button>
      )}
    </div>
  );
}
