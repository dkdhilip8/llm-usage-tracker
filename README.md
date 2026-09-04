# LLM Usage Tracker — Demo

An **LLM gateway** demo: issue per-user virtual API keys with per-provider access control,
send requests through the proxy, and watch a dashboard of per-user token usage and cost.
OpenAI · Anthropic · OpenRouter.

> **Demo only — not for production.** Ships in **simulated** mode (no real provider calls, no
> API keys, $0 cost). Auth is a single shared admin token — there is no user system.
>
> **Public vs admin — one shared dataset, not multi-tenant.** Every visitor sees the *same*
> pre-populated demo data; there's no signup and nothing is scoped to "your" login. **Dashboard,
> Requests and Insights are read-only and public** — no token needed. **Admin is the only gate**:
> create/revoke keys, change budgets, and reset the demo dataset all require the admin token.
> **Playground** sends real requests through the gateway, so it's admin-only too. Public visitors
> cannot write anything — the proxy endpoints still require a valid `vk_…` key, and no UI path
> hands one out.
>
> **Cost**: for **live OpenRouter** calls it's the provider's *actual* charge (`usage.cost` from
> the response). OpenAI/Anthropic don't return a per-request cost, so those (and all simulated
> calls) are **estimated** as tokens × a hand-maintained price table. Each row records which:
> `cost_source` = `provider` or `configured`. The dashboard shows the actual/estimated split.
>
> Real provider calls are wired but **off by default**. A request only hits a real provider when
> `ENABLE_LIVE=true` **and** the virtual key has `allow_live` **and** that provider's server-side
> key is configured and passes a liveness check. Otherwise it falls back to simulated — the
> public deployment keeps `ENABLE_LIVE=false`.

```
Browser → React (Dashboard · Requests · Insights — public)  +  (Playground · Admin — token)
        → FastAPI gateway
            → virtual-key auth  →  per-key provider ACL  →  per-key monthly budget
            → simulator (default)  |  real provider call (gated)   [JSON or SSE stream]
        → PostgreSQL (virtual_keys, allowed_providers, usage_logs) — one shared dataset
        → dashboard aggregates + request log (public reads) · demo reset (admin write)
```

- **OpenAI-compatible** — point any OpenAI SDK at `/v1`:

  ```python
  from openai import OpenAI
  client = OpenAI(base_url="http://localhost:8000/v1", api_key="vk_...")
  client.chat.completions.create(
      model="openrouter/meta-llama/llama-3.3-70b-instruct",   # <provider>/<slug>
      messages=[{"role": "user", "content": "hi"}],
      stream=True,   # SSE streaming supported
  )
  ```

- **Frontend:** React + Vite + TypeScript + Tailwind + Recharts
- **Backend:** FastAPI + SQLAlchemy 2.0 (sync) + psycopg3
- **DB:** PostgreSQL 16
- **Deploy:** one Docker image (FastAPI serves the built SPA) + one managed Postgres
- **CI:** GitHub Actions — ruff + pytest (Postgres service), tsc + vitest + build, `docker build`

---

## Run locally

Requires Docker.

```bash
docker compose up --build
```

| Service   | URL                          |
|-----------|------------------------------|
| Frontend  | http://localhost:5173        |
| Backend   | http://localhost:8000        |
| API docs  | http://localhost:8000/docs   |
| Adminer   | `docker compose --profile tools up` → http://localhost:8080 (server `db`, user/pass `llm`) |

On first boot the backend creates the schema and starts **empty** (`SEED_DEMO_DATA=false`
locally). Demo flow:

1. **Admin** (local dev token `dev-admin-token`) → **Demo tools → Reset demo dataset** — builds
   the shared dataset: 5 keys, ~30 days of simulated usage across all three providers, and a
   built-in anomaly. This is the same one-click reset the public deployment uses, and it's what
   every visitor sees — there's no per-visitor data.
2. **Dashboard**, **Requests**, **Insights** — open in a private tab with no admin token. All
   three are fully public: summary + charts + latency/error metrics, the per-request log, and
   anomaly alerts with **Investigate** → ranked contributors → **View related requests**.
3. **Playground** and the rest of **Admin** (create/revoke keys, budgets, providers) need the
   token — paste a `vk_…` key from a key you create to send a few requests, or point an OpenAI
   SDK at `http://localhost:8000/v1`.

*(`Admin → Demo tools → Inject usage spike` is the smaller, older helper — it just adds a
last-24h anomaly on top of whatever data already exists, without touching the rest.)*

Reset everything (wipes the DB):

```bash
docker compose down -v && docker compose up --build
```

### Tests

```bash
cd backend && pip install -r requirements-dev.txt
TEST_DATABASE_URL=postgresql+psycopg://llm:llm@localhost:5432/llmtracker_test pytest   # needs the compose db up
ruff check app tests

cd ../frontend && npm ci && npm run typecheck && npm test
```

CI (`.github/workflows/ci.yml`) runs all of the above plus `docker build` on every push.

### Smoke test

```bash
BASE=http://localhost:8000
curl -s $BASE/healthz
# public reads — no token
curl -s $BASE/api/usage/summary
curl -s $BASE/api/requests
curl -s $BASE/api/insights/alerts
# admin mutation without a token -> 403
curl -s -o /dev/null -w '%{http_code}\n' -X POST $BASE/api/demo/reset
# provider config status (admin)
curl -s $BASE/api/providers -H 'X-Admin-Token: dev-admin-token'
# create a key scoped to openai + openrouter
KEY=$(curl -s -X POST $BASE/api/keys -H 'X-Admin-Token: dev-admin-token' \
  -H 'Content-Type: application/json' \
  -d '{"label":"smoke","allowed_providers":["openai","openrouter"]}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["key"])')
# allowed provider -> simulated response + usage
curl -s -X POST $BASE/v1/proxy/chat -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"provider":"openai","model":"gpt-4o-mini","prompt":"hello from the demo"}'
# provider not on the key -> 403
curl -s -o /dev/null -w '%{http_code}\n' -X POST $BASE/v1/proxy/chat \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"provider":"anthropic","model":"claude-3-5-haiku","prompt":"hi"}'
# no key -> 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST $BASE/v1/proxy/chat \
  -H 'Content-Type: application/json' -d '{"provider":"openai","model":"gpt-4o","prompt":"x"}'
curl -s "$BASE/api/usage/summary"
```

---

## Deploy (100% free — the full cost breakdown)

| Item | Provider | Cost | Card? |
|---|---|---|---|
| Container web service | Render **Free** web service (Docker) | $0 | No |
| PostgreSQL | Neon **Free** plan (permanent, 0.5 GB) | $0 | No |
| Repo + blueprint | GitHub public repo | $0 | No |
| Public URL | `*.onrender.com` subdomain | $0 | No |
| Warmup pings | UptimeRobot / cron-job.org / GitHub Actions cron | $0 | No |
| LLM usage | none — simulated only | $0 | — |

The only "cost" of the free tier is a ~30–60 s cold start after 15 min idle (Render) and a
~300 ms cold DB wake (Neon).

### Steps (Render + Neon)

1. Create a **Neon** project (free) → copy the pooled connection string.
2. Push this repo to GitHub.
3. Render → **New → Blueprint** → select the repo. It reads `render.yaml` and creates the
   Docker web service with generated `ADMIN_TOKEN` / `SECRET_KEY`.
4. Set `DATABASE_URL` on the service to the Neon connection string, then deploy. `render.yaml`
   sets `SEED_DEMO_DATA=true`, so first boot builds the shared demo dataset automatically —
   every visitor sees it immediately, no manual step needed.
5. Open `https://<service>.onrender.com/healthz` → `{"status":"ok",...}`. Dashboard, Requests and
   Insights are public; the admin token (Render → service → Environment) unlocks `/admin` and
   `/playground`.

**All-Render fallback:** uncomment the `databases:` block in `render.yaml`. Note Render's free
Postgres is deleted 30 days after creation; the schema is recreated automatically but any keys
and usage you added are lost.

### Keep it warm before a demo

```bash
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" https://<service>.onrender.com/healthz
```

Or point a free UptimeRobot / cron-job.org monitor at `/healthz` every 10 minutes.

---

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local compose value | Postgres. `postgres://` / `postgresql://` auto-rewritten to the psycopg3 driver. |
| `ADMIN_TOKEN` | `dev-admin-token` | `X-Admin-Token` for key management + provider status. |
| `SECRET_KEY` | `dev-secret` | HMAC pepper for virtual-key hashing. |
| `CORS_ORIGINS` | `""` | Comma-separated origins; local dev only. |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` | `""` | Server-side provider creds. Blank ⇒ provider shows "not configured" and stays simulated. Never stored in the DB or shown in the UI. |
| `ENABLE_LIVE` | `false` | Master switch for real upstream calls. Keep `false` for the public demo. |
| `PROVIDER_CHECK_TTL` | `300` | Seconds to cache a provider liveness check. |
| `LOG_BODIES` | `false` | Store truncated prompt/response previews on `usage_logs` for the (public) Requests tab. Off by default and on the public deploy — bodies can be sensitive. |
| `SIMULATE_LATENCY_SLEEP` | `true` | Simulator sleeps to mimic real latency. Tests set `false`. |
| `SEED_DEMO_DATA` | `false` | On boot, if `usage_logs` is empty, build the shared demo dataset (same as *Admin → Demo tools → Reset demo dataset*). `true` on the public deploy so a fresh database self-populates; never overwrites existing data. |

---

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/healthz` | — | health / warmup |
| GET | `/api/models` | — | configured pricing table (per provider/model) |
| GET | `/api/providers` | `X-Admin-Token` | `[{provider, env_var, configured, valid, checked_at}]` (`?refresh=true` to re-check) |
| POST | `/api/keys` | `X-Admin-Token` | `{label, allowed_providers[], allow_live?, default_provider?, monthly_budget_usd?, budget_period?(day\|week\|month\|custom), budget_start?, budget_end?}` → raw key once |
| GET `\|` PATCH `\|` DELETE | `/api/keys[/{id}]` | `X-Admin-Token` | list (+ `spend_period`) / patch any of the above (`clear_budget`) / revoke |
| GET | `/api/requests` | — *(public)* | recent request log (`?limit&cursor&provider&model&key_id&status&mode&start&end`) |
| GET | `/api/insights/alerts` `\|` `/alerts/{id}` | — *(public)* | anomaly alerts / investigation (contributors + analysis + `related_query`) |
| POST | `/api/insights/demo-spike` | `X-Admin-Token` | add a last-24h anomaly on top of existing data (small demo helper) |
| POST | `/api/demo/reset` | `X-Admin-Token` | wipe **all** keys + usage and rebuild the deterministic shared demo dataset (5 keys, ~30 days, one built-in spike) — this is what every public visitor sees |
| **POST** | **`/v1/chat/completions`** | Bearer `vk_…` | **OpenAI-compatible.** `{model:"<provider>/<slug>", messages[], stream?}` → OpenAI `chat.completion` (or SSE chunks). `402` over budget, `403` provider not on key. |
| GET | `/v1/proxy/inspect` | Bearer `vk_…` | this key's allowed providers + per-provider `mode` (`simulated`/`live`) |
| POST | `/v1/proxy/chat` | Bearer `vk_…` | friendly shape used by the Playground: `{provider, model, prompt}` → completion + usage |
| GET | `/api/usage/summary` `\|` `/timeseries` `\|` `/by-key` `\|` `/by-model` | — *(public)* | dashboard aggregates (`start,end,provider,model,key_id`); `summary` adds `latency_p50_ms`, `latency_p95_ms`, `error_rate`, `tokens_per_sec` |

Public endpoints are read-only demo data — `LOG_BODIES=false` on the public deploy keeps prompt/
response previews out of `/api/requests` regardless. Every other write (keys, budgets, providers,
demo reset) requires `X-Admin-Token`, and the proxy (`/v1/*`) requires a `vk_…` key that only the
admin can mint — so a public visitor has no path to modify the shared dataset.

---

## Data model

- **virtual_keys** — `id, label, key_hash, key_prefix, allow_live, default_provider,
  monthly_budget_usd, budget_period, budget_start, budget_end, created_at,
  last_used_at, revoked_at`. Only the HMAC hash and an 11-char prefix are stored; the raw key is
  shown once. Per-key **budget**: `$X` per `1 day` / `1 week` / `1 month` (rolling), or a
  `custom` fixed `[budget_start, budget_end]` range → `402` when spent (outside a custom range
  the cap doesn't apply). Keys don't expire — revoke them explicitly.
- **allowed_providers** — `(virtual_key_id, provider)`, unique. The per-key provider ACL enforced
  on every proxied request.
- **usage_logs** — `id, key_id, request_id, provider, model, prompt_tokens, completion_tokens,
  total_tokens, cost, cost_source, mode, simulated, latency_ms, status, prompt_preview,
  response_preview, ts`. `cost_source` = `provider` (real charge, e.g. OpenRouter `usage.cost`) or
  `configured` (tokens × price table). Previews are null unless `LOG_BODIES=true`.

Provider credentials live only in server env vars — never in these tables, never sent to the
browser. `/api/providers` reports presence + liveness as booleans only.

**The demo dataset is shared, not per-user.** `POST /api/demo/reset` (`app/demo.py`) deletes every
row and deterministically rebuilds 5 keys (Engineering, Data Science, Support Bot, Content Team,
Mobile App) with ~30 days of simulated traffic across all three providers, a weekday/weekend
pattern, a ~1.5% error rate, and a built-in last-24h cost spike on Engineering so Insights always
has something to investigate. The demo keys' `key_hash` is random (`secrets.token_hex(32)`) and no
raw key is ever produced — they exist only to own usage rows, not to authenticate anything.

---

## Future improvements

Per-key rate limits (RPM/TPM) · per-model budgets · webhook/Slack alerts + scheduled anomaly detection ·
LLM-written insight narratives · Prometheus `/metrics` · pricing catalog auto-synced from
OpenRouter `/api/v1/models` · exact-match response cache · provider fallback on live error ·
Alembic migrations + backups · Redis for shared rate-limit / budget counters · SSO / org
hierarchy · OpenTelemetry traces.
