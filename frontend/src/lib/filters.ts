export interface Filters {
  rangeDays: number; // 0 = all time
  provider: string;
  model: string;
  keyId: string;
}

export const defaultFilters: Filters = {
  rangeDays: 14,
  provider: "",
  model: "",
  keyId: "",
};

export function toQuery(f: Filters): string {
  const p = new URLSearchParams();
  if (f.rangeDays > 0) {
    p.set("start", new Date(Date.now() - f.rangeDays * 86_400_000).toISOString());
  }
  if (f.provider) p.set("provider", f.provider);
  if (f.model) p.set("model", f.model);
  if (f.keyId) p.set("key_id", f.keyId);
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const RANGE_OPTIONS = [
  { label: "7d", value: 7 },
  { label: "14d", value: 14 },
  { label: "30d", value: 30 },
  { label: "All", value: 0 },
];
