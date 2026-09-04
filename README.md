# LLM Usage Tracker — Demo

An **LLM gateway** demo: issue per-user virtual API keys with per-provider access control,
send requests through the proxy, and watch a dashboard of per-user token usage and cost.
OpenAI · Anthropic · OpenRouter.

> **Demo only — not for production.** Ships in **simulated** mode (no real provider calls, no
> API keys, $0 cost). Auth is a single shared admin token. Data is disposable — the app starts
> **empty**; you create keys and generate usage yourself.
>
> **Cost**: for **live OpenRouter** calls it's the provider's *actual* charge (`usage.cost` from
> the response). OpenAI/Anthropic don't return a per-request cost, so those (and all simulated
> calls) are **estimated** as tokens × a hand-maintained price table. Each row records which:
> `cost_source` = `provider` or `configured`. The dashboard shows the actual/estimated split.
>
> Real provider calls are wired but **off by default**. A request only hits a real provider when
> `ENABLE_LIVE=true` **and** the virtual key has `allow_live` **and** that provider's server-side
> key is configured and passes a liveness check. Otherwise it falls back to simulated.

```
Browser → React (Dashboard · Playground · Requests · Admin)
        → FastAPI gateway
            → virtual-key auth  →  per-key provider ACL  →  per-key monthly budget
            → simulator (default)  |  real provider call (gated)   [JSON or SSE stream]
        → PostgreSQL (virtual_keys, allowed_providers, usage_logs)
        → dashboard aggregates + request log
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

On first boot the backend creates the schema. Everything starts **empty**. Demo flow:

1. **Admin** (local dev token `dev-admin-token`) → check the Providers panel → create a virtual
   key: label, allowed providers, optional default provider + monthly budget.
2. **Playground** → paste the `vk_…` key → pick an allowed provider + model → send a few requests
   (or point an OpenAI SDK at `http://localhost:8000/v1`).
3. **Dashboard** → summary + latency p50/p95 + error rate + tokens/sec + charts, live.
4. **Requests** (admin) → per-request log; click a row for the detail drawer.

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
4. Set `DATABASE_URL` on the service to the Neon connection string, then deploy.
5. Open `https://<service>.onrender.com/healthz` → `{"status":"ok",...}`. The dashboard is
   public; the admin token (Render → service → Environment) unlocks `/admin`.

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
| `LOG_BODIES` | `false` | Store truncated prompt/response previews on `usage_logs` for the Requests tab. Off by default (bodies can be sensitive). |
| `SIMULATE_LATENCY_SLEEP` | `true` | Simulator sleeps to mimic real latency. Tests set `false`. |

---

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/healthz` | — | health / warmup |
| GET | `/api/models` | — | configured pricing table (per provider/model) |
| GET | `/api/providers` | `X-Admin-Token` | `[{provider, env_var, configured, valid, checked_at}]` (`?refresh=true` to re-check) |
| POST | `/api/keys` | `X-Admin-Token` | `{label, allowed_providers[], allow_live?, default_provider?, monthly_budget_usd?}` → raw key shown once |
| GET `\|` PATCH `\|` DELETE | `/api/keys[/{id}]` | `X-Admin-Token` | list (+ `spend_month`) / `{allow_live, default_provider, monthly_budget_usd, clear_budget}` / revoke |
| GET | `/api/requests` | `X-Admin-Token` | recent request log (`?limit&cursor&provider&model&key_id&status&mode`) |
| **POST** | **`/v1/chat/completions`** | Bearer `vk_…` | **OpenAI-compatible.** `{model:"<provider>/<slug>", messages[], stream?}` → OpenAI `chat.completion` (or SSE chunks). `402` over budget, `403` provider not on key. |
| GET | `/v1/proxy/inspect` | Bearer `vk_…` | this key's allowed providers + per-provider `mode` (`simulated`/`live`) |
| POST | `/v1/proxy/chat` | Bearer `vk_…` | friendly shape used by the Playground: `{provider, model, prompt}` → completion + usage |
| GET | `/api/usage/summary` `\|` `/timeseries` `\|` `/by-key` `\|` `/by-model` | — | dashboard aggregates (`start,end,provider,model,key_id`); `summary` adds `latency_p50_ms`, `latency_p95_ms`, `error_rate`, `tokens_per_sec` |

---

## Data model

- **virtual_keys** — `id, label, key_hash, key_prefix, allow_live, default_provider,
  monthly_budget_usd, created_at, last_used_at, revoked_at`. Only the HMAC hash and an 11-char
  prefix are stored; the raw key is shown once. `monthly_budget_usd` → `402` once month-to-date
  spend reaches it.
- **allowed_providers** — `(virtual_key_id, provider)`, unique. The per-key provider ACL enforced
  on every proxied request.
- **usage_logs** — `id, key_id, request_id, provider, model, prompt_tokens, completion_tokens,
  total_tokens, cost, cost_source, mode, simulated, latency_ms, status, prompt_preview,
  response_preview, ts`. `cost_source` = `provider` (real charge, e.g. OpenRouter `usage.cost`) or
  `configured` (tokens × price table). Previews are null unless `LOG_BODIES=true`.

Provider credentials live only in server env vars — never in these tables, never sent to the
browser. `/api/providers` reports presence + liveness as booleans only.

---

## Future improvements

Per-key rate limits (RPM) · budget-threshold alerts/webhooks · Prometheus `/metrics` · pricing
catalog auto-synced from OpenRouter `/api/v1/models` · exact-match response cache · provider
fallback on live error · Alembic migrations + backups · Redis for shared counters · SSO / org
hierarchy · OpenTelemetry traces.
