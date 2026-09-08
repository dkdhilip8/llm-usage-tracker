const BASE = import.meta.env.VITE_API_BASE_URL ?? "";

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    credentials: "include", // carry the session cookie
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

// ---- types ----
export const PROVIDERS = ["openai", "anthropic", "openrouter", "gemini"] as const;
export type Provider = (typeof PROVIDERS)[number];
export type WorkspaceRole = "admin" | "member";

export interface ModelInfo {
  provider: string;
  model: string;
  input_per_1m: number;
  output_per_1m: number;
}

export interface ProviderEnvStatus {
  provider: string;
  env_var: string;
  configured_via_env: boolean;
}

export interface WorkspaceRef {
  id: number;
  name: string;
  role: WorkspaceRole;
}

export interface AuthUser {
  id: number;
  username: string;
  workspace: WorkspaceRef | null;
}

export interface Account {
  username: string;
  workspace: WorkspaceRef | null;
  live_cap_default_usd: number;
}

export interface WorkspaceProvider {
  provider: string;
  configured: boolean;
  source: "env" | "workspace" | "none";
  last4: string | null;
  monthly_cap_usd: number | null; // null => the default applies
  spend_this_month: number;
}

export interface WorkspaceMember {
  user_id: number;
  username: string;
  role: WorkspaceRole;
  assigned_keys: number;
  requests: number;
  cost: number;
}

export interface WorkspaceInvite {
  id: number;
  code: string;
  label: string | null;
  created_at?: string;
  expires_at: string;
}

export interface WorkspaceDetail {
  id: number;
  name: string;
  role: WorkspaceRole;
  members?: WorkspaceMember[];
  invites?: WorkspaceInvite[];
  providers?: WorkspaceProvider[];
}

export type BudgetPeriod = "day" | "week" | "month" | "custom";

export interface KeyRow {
  id: number;
  label: string;
  key_prefix: string;
  assigned_username: string | null;
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

export const api = {
  health: () =>
    req<{ status: string; version: string; live_enabled: boolean }>("/healthz"),
  models: () => req<ModelInfo[]>("/api/models"),

  // ---- auth ----
  me: () => req<{ authenticated: boolean; user: AuthUser | null }>("/api/auth/me"),
  login: (username: string, password: string) =>
    req<{ authenticated: boolean; user: AuthUser }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  signup: (username: string, password: string) =>
    req<{ authenticated: boolean; user: AuthUser }>("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => req<{ authenticated: boolean }>("/api/auth/logout", { method: "POST" }),

  // ---- account (personal) ----
  getAccount: () => req<Account>("/api/account"),
  deleteAccount: () => req<{ deleted: boolean }>("/api/account", { method: "DELETE" }),

  // ---- workspace ----
  getWorkspace: () => req<WorkspaceDetail>("/api/workspace"),
  createWorkspace: (name: string) =>
    req<WorkspaceDetail>("/api/workspace", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  renameWorkspace: (name: string) =>
    req<WorkspaceDetail>("/api/workspace", {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  deleteWorkspace: () => req<{ deleted: boolean }>("/api/workspace", { method: "DELETE" }),
  leaveWorkspace: () => req<{ left: boolean }>("/api/workspace/leave", { method: "POST" }),
  joinWorkspace: (code: string) =>
    req<WorkspaceDetail>("/api/workspace/join", {
      method: "POST",
      body: JSON.stringify({ code }),
    }),
  createInvite: (label?: string) =>
    req<WorkspaceInvite>("/api/workspace/invites", {
      method: "POST",
      body: JSON.stringify({ label: label?.trim() || null }),
    }),
  revokeInvite: (id: number) =>
    req<{ revoked: boolean }>(`/api/workspace/invites/${id}`, { method: "DELETE" }),
  removeMember: (userId: number) =>
    req<{ removed: boolean }>(`/api/workspace/members/${userId}`, { method: "DELETE" }),
  workspaceProviders: () => req<WorkspaceProvider[]>("/api/workspace/providers"),
  setWorkspaceProviderKey: (provider: string, apiKey: string) =>
    req<{ provider: string; last4: string; valid: boolean }>(
      `/api/workspace/providers/${provider}/key`,
      { method: "PUT", body: JSON.stringify({ api_key: apiKey }) },
    ),
  clearWorkspaceProviderKey: (provider: string) =>
    req<{ provider: string }>(`/api/workspace/providers/${provider}/key`, {
      method: "DELETE",
    }),
  setWorkspaceProviderCap: (provider: string, monthlyCapUsd: number | null) =>
    req<WorkspaceProvider[]>(`/api/workspace/providers/${provider}/cap`, {
      method: "PATCH",
      body: JSON.stringify({ monthly_cap_usd: monthlyCapUsd }),
    }),

  // ---- providers (instance-wide env status; admin) ----
  providers: () => req<ProviderEnvStatus[]>("/api/providers"),

  usageSummary: (qs: string) => req<UsageSummary>(`/api/usage/summary${qs}`),
  usageTimeseries: (qs: string) => req<TimeseriesPoint[]>(`/api/usage/timeseries${qs}`),
  usageByKey: (qs: string) => req<ByKeyRow[]>(`/api/usage/by-key${qs}`),
  usageByModel: (qs: string) => req<ByModelRow[]>(`/api/usage/by-model${qs}`),

  listRequests: (qs: string) =>
    req<{ items: RequestRow[]; next_cursor: number | null; bodies_logged: boolean }>(
      `/api/requests${qs}`,
    ),

  listKeys: () => req<KeyRow[]>("/api/keys"),
  createKey: (input: {
    label: string;
    allowed_providers: string[];
    allow_live: boolean;
    default_provider?: string | null;
    assigned_user_id?: number | null;
    monthly_budget_usd?: number | null;
    budget_period?: BudgetPeriod;
    budget_start?: string | null;
    budget_end?: string | null;
  }) =>
    req<KeyCreated>("/api/keys", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  updateKey: (
    id: number,
    patch: {
      allow_live?: boolean;
      default_provider?: string | null;
      assigned_user_id?: number | null;
      clear_assignment?: boolean;
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
    }),
  revokeKey: (id: number) =>
    req<{ id: number; revoked_at: string }>(`/api/keys/${id}`, {
      method: "DELETE",
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
