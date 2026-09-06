export interface Filters {
  rangeDays: number; // 0 = all time; ignored when `start` is set
  start: string; // "YYYY-MM-DD" custom-range start (empty => use rangeDays)
  end: string; // "YYYY-MM-DD" custom-range end, inclusive
  provider: string;
  model: string;
  keyId: string;
}

export const DEFAULT_RANGE_DAYS = 30;

export const defaultFilters: Filters = {
  rangeDays: DEFAULT_RANGE_DAYS,
  start: "",
  end: "",
  provider: "",
  model: "",
  keyId: "",
};

/** today (0) or N days ago, as YYYY-MM-DD in UTC */
export function isoDate(daysAgo = 0): string {
  return new Date(Date.now() - daysAgo * 86_400_000).toISOString().slice(0, 10);
}

export function toQuery(f: Filters): string {
  const p = new URLSearchParams();
  if (f.start) {
    p.set("start", new Date(f.start + "T00:00:00Z").toISOString());
    if (f.end) {
      const e = new Date(f.end + "T00:00:00Z");
      e.setUTCDate(e.getUTCDate() + 1); // end date is inclusive
      p.set("end", e.toISOString());
    }
  } else if (f.rangeDays > 0) {
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
  { label: "30d", value: 30 },
  { label: "All", value: 0 },
];
