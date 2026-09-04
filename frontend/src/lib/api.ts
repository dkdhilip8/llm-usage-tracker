const BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export function getAdminToken(): string {
  try {
    return localStorage.getItem("admin_token") ?? "";
  } catch {
    return "";
  }
}

export function setAdminToken(token: string): void {
  try {
    localStorage.setItem("admin_token", token);
  } catch {
    /* private mode / storage blocked — nothing to do */
  }
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      detail = (await res.json())?.detail ?? detail;
    } catch {
      /* body was not JSON */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return (await res.json()) as T;
}

function adminHeaders(): Record<string, string> {
  return { "X-Admin-Token": getAdminToken() };
}

// ---- types ----
export const PROVIDERS = ["openai", "anthropic", "openrouter"] as const;
export type Provider = (typeof PROVIDERS)[number];

export interface ModelInfo {
  provider: string;
  model: string;
  input_per_1m: number;
  output_per_1m: number;
}

export interface ProviderStatus {
  provider: string;
  env_var: string;
  configured: boolean;
  valid: boolean;
  checked_at: string | null;
}

export type BudgetPeriod = "day" | "week" | "month" | "custom";

export interface KeyRow {
  id: number;
  label: string;
  key_prefix: string;
  allowed_providers: string[];
  allow_live: boolean;
  default_provider: string | null;
  monthly_budget_usd: number | null;
  budget_period: BudgetPeriod;
  budget_start: string | null;
  budget_end: string | null;
  spend_period: number;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  requests: number;
  total_tokens: number;
  cost: number;
}

export interface KeyCreated {
  id: number;
  label: string;
  key: string;
  key_prefix: string;
  allowed_providers: string[];
  allow_live: boolean;
  default_provider: string | null;
  monthly_budget_usd: number | null;
  budget_period: BudgetPeriod;
  budget_start: string | null;
  budget_end: string | null;
  created_at: string;
}

export interface RequestRow {
  id: number;
  ts: string;
  request_id: string;
  key_id: number;
  key_label: string;
  key_prefix: string;
  provider: string;
  model: string;
  mode: "simulated" | "live";
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost: number;
  cost_source: "provider" | "configured";
  latency_ms: number;
  status: string;
  prompt_preview: string | null;
  response_preview: string | null;
}

export interface KeyInspect {
  label: string;
  allow_live: boolean;
  providers: {
    provider: string;
    configured: boolean;
    valid: boolean;
    mode: "simulated" | "live";
  }[];
}

export interface UsageSummary {
  total_requests: number;
  total_tokens: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_cost: number;
  cost_actual: number;
  cost_estimated: number;
  latency_p50_ms: number;
  latency_p95_ms: number;
  error_rate: number;
  errors: number;
  tokens_per_sec: number;
  active_keys: number;
}

export interface TimeseriesPoint {
  day: string;
  provider: string;
  requests: number;
  total_tokens: number;
  cost: number;
}

export interface ByKeyRow {
  key_id: number;
  label: string;
  key_prefix: string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost: number;
  last_used_at: string | null;
}

export interface ByModelRow {
  provider: string;
  model: string;
  requests: number;
  total_tokens: number;
  cost: number;
}

export interface ChatResult {
  request_id: string;
  provider: string;
  model: string;
  mode: "simulated" | "live";
  simulated: boolean;
  response: string;
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
  cost: number;
  cost_source: "provider" | "configured";
  pricing: { input_per_1m: number; output_per_1m: number; source: string };
  latency_ms: number;
}

export interface InsightAlert {
  id: string;
  type: "cost_spike" | "token_spike";
  severity: "warning" | "critical";
  title: string;
  detail: string;
  metric: "cost" | "tokens";
  current: number;
  baseline: number;
  pct_change: number;
  scope: {
    kind: "key" | "model" | "global";
    key_id?: number;
    key_label?: string;
    provider?: string;
    model?: string;
  };
}

export interface Contributor {
  label: string;
  kind: "key" | "model" | "volume";
  pct: number;
  detail: string;
}

export interface Investigation {
  alert_id: string;
  metric: "cost" | "tokens";
  headline: {
    metric: "cost" | "tokens";
    baseline: number;
    current: number;
    pct_change: number;
  };
  contributors: Contributor[];
  summary: string;
  related_query: Record<string, string | number>;
  analyzed: string[];
}

export const api = {
  health: () =>
    req<{ status: string; version: string; live_enabled: boolean }>("/healthz"),
  models: () => req<ModelInfo[]>("/api/models"),

  insights: () =>
    req<{ alerts: InsightAlert[] }>("/api/insights/alerts", { headers: adminHeaders() }),
  investigate: (id: string) =>
    req<Investigation>(`/api/insights/alerts/${encodeURIComponent(id)}`, {
      headers: adminHeaders(),
    }),
  injectDemoSpike: () =>
    req<{ inserted: number; key_label: string }>("/api/insights/demo-spike", {
      method: "POST",
      headers: adminHeaders(),
    }),

  providers: (refresh = false) =>
    req<ProviderStatus[]>(`/api/providers${refresh ? "?refresh=true" : ""}`, {
      headers: adminHeaders(),
    }),

  usageSummary: (qs: string) => req<UsageSummary>(`/api/usage/summary${qs}`),
  usageTimeseries: (qs: string) => req<TimeseriesPoint[]>(`/api/usage/timeseries${qs}`),
  usageByKey: (qs: string) => req<ByKeyRow[]>(`/api/usage/by-key${qs}`),
  usageByModel: (qs: string) => req<ByModelRow[]>(`/api/usage/by-model${qs}`),

  listRequests: (qs: string) =>
    req<{ items: RequestRow[]; next_cursor: number | null; bodies_logged: boolean }>(
      `/api/requests${qs}`,
      { headers: adminHeaders() },
    ),

  listKeys: () => req<KeyRow[]>("/api/keys", { headers: adminHeaders() }),
  createKey: (input: {
    label: string;
    allowed_providers: string[];
    allow_live: boolean;
    default_provider?: string | null;
    monthly_budget_usd?: number | null;
    budget_period?: BudgetPeriod;
    budget_start?: string | null;
    budget_end?: string | null;
  }) =>
    req<KeyCreated>("/api/keys", {
      method: "POST",
      body: JSON.stringify(input),
      headers: adminHeaders(),
    }),
  updateKey: (
    id: number,
    patch: {
      allow_live?: boolean;
      default_provider?: string | null;
      monthly_budget_usd?: number | null;
      budget_period?: BudgetPeriod;
      budget_start?: string | null;
      budget_end?: string | null;
      clear_budget?: boolean;
    },
  ) =>
    req<Record<string, unknown>>(`/api/keys/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
      headers: adminHeaders(),
    }),
  revokeKey: (id: number) =>
    req<{ id: number; revoked_at: string }>(`/api/keys/${id}`, {
      method: "DELETE",
      headers: adminHeaders(),
    }),

  inspectKey: (key: string) =>
    req<KeyInspect>("/v1/proxy/inspect", {
      headers: { Authorization: `Bearer ${key}` },
    }),
  proxyChat: (
    payload: { provider: string; model: string; prompt: string },
    key: string,
  ) =>
    req<ChatResult>("/v1/proxy/chat", {
      method: "POST",
      body: JSON.stringify(payload),
      headers: { Authorization: `Bearer ${key}` },
    }),
};
