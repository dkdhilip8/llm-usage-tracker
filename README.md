# LLM Usage Tracker — Demo

An **LLM gateway** demo: issue per-user virtual API keys with per-provider access control,
send requests through the proxy, and watch a dashboard of per-user token usage and cost.
OpenAI · Anthropic · OpenRouter.

> **Demo only — not for production.** **Simulated** by default (no real provider calls, no host
> API keys, $0 to run). Real calls happen only when a user attaches **their own** provider key,
> under a spend cap — see below.
>
> **Multi-tenant.** Anyone can **sign up** (email + password, scrypt-hashed) and gets their own
> virtual keys, Playground, and a private dashboard seeded with a small sample dataset.
> **Logged out**, the Dashboard / Requests / Insights tabs show a shared **demo** account's data
> (read-only). **Logged in**, they show only *your* usage. One privileged **admin** account
> (`ADMIN_USERNAME` + `ADMIN_PASSWORD`, or the `X-Admin-Token` header for curl / SDK / CI) sees
> everything and owns provider config + the demo-data reset.
>
> **Per-account live mode.** On the Account page a signed-in user pastes their **own**
> OpenAI/Anthropic/OpenRouter key (encrypted at rest, shown back only as `····last4`) and sets a
> monthly spend cap. Their virtual keys can then be flagged **allow live** and make real upstream
> calls billed to that key, blocked once the month's live spend hits the cap (default
> `LIVE_CAP_DEFAULT_USD`, hard ceiling `LIVE_CAP_MAX_USD` = $10). They copy the raw `vk_…` strings
> and hand them to whoever needs them — **recipients need no account**. Users with no attached key
> stay **simulated**. The host sets no server-side provider key, so nothing is billed to the
> deployment.
>
> Abuse controls for the public database: `MAX_USERS`, signups/IP/hour, per-account caps on keys /
> stored usage rows / requests-per-hour, and the server-enforced per-account live-spend cap. No
> email verification, no password reset — it's a demo.
>
> **Provider keys** normally come from server env vars only. Locally / non-prod an admin can set
> `ALLOW_DB_PROVIDER_KEYS=true` and paste keys in the UI — stored **AES-encrypted** in the DB,
> shown back only as `····last4`, always overridden by an env var for the same provider.
> **Hard-blocked when `ENVIRONMENT` is deployed.**
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
- **CI config:** [`.github/workflows/ci.yml`](.github/workflows/ci.yml) defines ruff + pytest
  (Postgres service), tsc + vitest + build, and `docker build`. Not yet enabled on the remote —
  push it once the repo has GitHub Actions access.

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

On first boot the backend creates the schema (`SEED_DEMO_DATA=false` locally). Demo flow:

1. **Dashboard / Requests / Insights** — open with no sign in. They show the shared **demo**
   account's data: summary + charts + latency/error metrics, the per-request log, and anomaly
   alerts with **Investigate** → ranked contributors → **View related requests**. (Empty until
   an admin seeds it — step 3.)
2. **Sign in → Create an account** — you land on **/account** with 3 seeded sample keys and a
   private dashboard. Open **Playground**, paste one of *your* `vk_…` keys, send requests — they
   show up only on *your* Dashboard/Requests/Insights, not other users' or the demo's.
3. **Admin** (`admin` / `dev-password` under docker-compose) → **Demo tools → Reset demo
   dataset** builds the shared logged-out dataset (5 keys, ~30 days, a built-in anomaly).
   Admin's Dashboard shows *all* accounts; the keys table gains an Owner column.

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

`.github/workflows/ci.yml` runs all of the above plus `docker build`. It is committed but
**not yet active on the remote** — enable GitHub Actions on the repo and push the workflow
(`gh auth refresh -h github.com -s workflow`) to turn it on.

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
| LLM usage | simulated by default; a user's live calls bill **their own** attached provider key (capped) — never the host | $0 to run | No |

The only "cost" of the free tier is a ~30–60 s cold start after 15 min idle (Render) and a
~300 ms cold DB wake (Neon).

### Steps (Render + Neon)

1. Create a **Neon** project (free) → copy the connection string.
2. Push this repo to GitHub.
3. Render → **New → Blueprint** → select the repo. It reads `render.yaml` and creates the
   Docker web service with `ENVIRONMENT=production` and generated `ADMIN_TOKEN` / `SECRET_KEY` /
   `ADMIN_PASSWORD` (the app refuses to boot on the dev defaults, or without a ≥12-char
   `ADMIN_PASSWORD`, once `ENVIRONMENT` is deployed). Creating the service by hand instead? Set
   `ENVIRONMENT`, `ADMIN_TOKEN`, `SECRET_KEY`, `ADMIN_PASSWORD` yourself.
4. Set `DATABASE_URL` on the service to the Neon connection string, then deploy. `render.yaml`
   sets `SEED_DEMO_DATA=true`, so first boot builds the shared demo dataset automatically —
   every visitor sees it immediately, no manual step needed.
5. Open `https://<service>.onrender.com/healthz` → `{"status":"ok",...}`. Dashboard/Requests/
   Insights are public (demo data); anyone can **sign up** for their own. Sign in as **admin**
   with `admin` + the generated `ADMIN_PASSWORD` (Render → service → Environment) to reset the
   demo data and see all accounts.

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
| `ENVIRONMENT` | `development` | Set to `production` (or `staging`) on a deployed host. The app then **refuses to start** unless `ADMIN_TOKEN` + `SECRET_KEY` are non-default, `ADMIN_PASSWORD` is set (≥12 chars), and `ALLOW_DB_PROVIDER_KEYS` is off. |
| `DATABASE_URL` | local compose value | Postgres. `postgres://` / `postgresql://` auto-rewritten to the psycopg3 driver. |
| `ADMIN_TOKEN` | `dev-admin-token` | `X-Admin-Token` header credential (curl / SDK / CI). Must be overridden when `ENVIRONMENT` is deployed. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `""` | Admin login form. Empty password ⇒ password login disabled (token still works). `ADMIN_PASSWORD` required (≥12 chars) when `ENVIRONMENT` is deployed. |
| `SESSION_TTL_HOURS` | `168` | Session-cookie lifetime (users + admin). |
| `ALLOW_SIGNUP` | `true` | Public account creation. |
| `MAX_USERS` | `300` | Signup is refused past this many real accounts (protects the free DB). |
| `SIGNUPS_PER_IP_PER_HOUR` | `5` | In-process signup throttle per client IP. |
| `MAX_KEYS_PER_USER` | `10` | Virtual-key cap per non-admin account. |
| `MAX_USAGE_ROWS_PER_USER` | `4000` | The proxy stops recording once an account hits this. |
| `PLAYGROUND_REQUESTS_PER_HOUR` | `120` | Per-account proxy rate limit (non-admin). |
| `ALLOW_LIVE_KEYS` | `true` | Let users attach their own provider key on the Account page to enable live mode for their virtual keys. |
| `LIVE_CAP_DEFAULT_USD` / `LIVE_CAP_MAX_USD` | `5` / `10` | Default / hard ceiling for an account's monthly live-spend cap. |
| `SECRET_KEY` | `dev-secret` | HMAC pepper for virtual-key hashing **and** session-cookie signing. Must be overridden when `ENVIRONMENT` is deployed. |
| `CORS_ORIGINS` | `""` | Comma-separated origins; local dev only. |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` | `""` | Server-side provider creds. Always win over a DB-stored key. Never shown in the UI. |
| `ALLOW_DB_PROVIDER_KEYS` | `false` | Let the Admin UI store provider keys (AES-encrypted) in the DB. Convenience for local / non-prod — **hard-blocked when `ENVIRONMENT` is deployed**. |
| `ENCRYPTION_KEY` | `""` | Fernet key (urlsafe-base64, 32 bytes) for that encryption. Empty ⇒ derived from `SECRET_KEY`. |
| `ENABLE_LIVE` | `false` | Master switch for real upstream calls. `true` on the deploy — but a request still needs its virtual key flagged `allow_live` **and** the owning account to have attached its own provider key, and it stops at that account's monthly cap. |
| `PROVIDER_CHECK_TTL` | `300` | Seconds to cache a provider liveness check. |
| `LOG_BODIES` | `false` | Store truncated prompt/response previews on `usage_logs` for the (public) Requests tab. Off by default and on the public deploy — bodies can be sensitive. |
| `SIMULATE_LATENCY_SLEEP` | `true` | Simulator sleeps to mimic real latency. Tests set `false`. |
| `SEED_DEMO_DATA` | `false` | On boot, if `usage_logs` is empty, build the shared demo dataset (same as *Admin → Demo tools → Reset demo dataset*). `true` on the public deploy so a fresh database self-populates; never overwrites existing data. |

---

## API

Auth = a valid session cookie (`POST /api/auth/{signup,login}`) **or** the `X-Admin-Token` header
(→ the admin user). "user" below = any signed-in account; "admin" = the privileged one. Read
endpoints are **viewer-scoped**: logged out → the demo account; user → their own; admin → all.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/healthz` | — | health / warmup (`{version, live_enabled, db_keys_enabled}`) |
| GET | `/api/models` | — | configured pricing table (per provider/model) |
| POST | `/api/auth/signup` `\|` `/login` | — | `{email, password}` → sets `httpOnly` session cookie, `{authenticated, user:{id,email,is_admin,can_live}}` |
| POST `\|` GET | `/api/auth/logout` `\|` `/me` | — | clear the cookie / current principal |
| POST `\|` DELETE `\|` DELETE | `/api/account/sample` `\|` `/api/account/data` `\|` `/api/account` | user | regenerate my sample data / wipe my keys+usage / delete my account |
| GET `\|` PATCH | `/api/account` | user | my account (email, `can_live`, `live_cap_usd`, `live_spend_this_month`, per-provider status) / set `{live_cap_usd}` (clamped to `LIVE_CAP_MAX_USD`) |
| PUT `\|` DELETE | `/api/account/providers/{provider}/key` | user | attach / clear **my own** encrypted provider key. `409` if a server env var is set for that provider |
| GET | `/api/providers` | admin | `[{provider, env_var, configured, valid, source, last4, checked_at}]` (`?refresh=true` to re-check) |
| PUT `\|` DELETE | `/api/providers/{provider}/key` | admin | set / clear a DB-stored (encrypted) provider key. `404` unless `ALLOW_DB_PROVIDER_KEYS`; `409` if a server env var is set for that provider |
| POST | `/api/keys` | user | `{label, allowed_providers[], allow_live?(needs an attached provider key), default_provider?, monthly_budget_usd?, budget_period?(day\|week\|month\|custom), budget_start?, budget_end?}` → raw key once |
| GET `\|` PATCH `\|` DELETE | `/api/keys[/{id}]` | user | list *your* keys (admin: all, + `owner_email`) / patch / revoke |
| GET | `/api/requests` | — *(scoped)* | recent request log (`?limit&cursor&provider&model&key_id&status&mode&start&end`) |
| GET | `/api/insights/alerts` `\|` `/alerts/{id}` | — *(scoped)* | anomaly alerts / investigation (contributors + analysis + `related_query`) |
| POST | `/api/insights/demo-spike` | admin | add a last-24h anomaly on top of existing data (small demo helper) |
| POST | `/api/demo/reset` | admin | wipe **all** keys + usage and rebuild the deterministic shared demo dataset (5 keys, ~30 days, one built-in spike) — this is what every public visitor sees |
| **POST** | **`/v1/chat/completions`** | Bearer `vk_…` | **OpenAI-compatible.** `{model:"<provider>/<slug>", messages[], stream?}` → OpenAI `chat.completion` (or SSE chunks). `402` over budget, `403` provider not on key. |
| GET | `/v1/proxy/inspect` | Bearer `vk_…` | this key's allowed providers + per-provider `mode` (`simulated`/`live`) |
| POST | `/v1/proxy/chat` | Bearer `vk_…` | friendly shape used by the Playground: `{provider, model, prompt}` → completion + usage |
| GET | `/api/usage/summary` `\|` `/timeseries` `\|` `/by-key` `\|` `/by-model` | — *(scoped)* | dashboard aggregates (`start,end,provider,model,key_id`); `summary` adds `latency_p50_ms`, `latency_p95_ms`, `error_rate`, `tokens_per_sec` |

Public endpoints are read-only demo data — `LOG_BODIES=false` on the public deploy keeps prompt/
response previews out of `/api/requests` regardless. Every other write (auth aside — login just
mints a cookie) requires admin auth, and the proxy (`/v1/*`) requires a `vk_…` key that only the
admin can mint — so a public visitor has no path to modify the shared dataset.

---

## Data model

- **users** — `id, email (unique), password_hash (scrypt), is_admin, is_demo, live_cap_usd,
  created_at`. The admin row is created/updated from `ADMIN_USERNAME`/`ADMIN_PASSWORD` on boot;
  the demo row owns the shared logged-out dataset and can't log in. Sessions are a stateless
  HMAC-signed cookie carrying the user id. `live_cap_usd` NULL ⇒ `LIVE_CAP_DEFAULT_USD` applies;
  `enforce_account_cap` blocks the account's live calls once the calendar month's `mode='live'`
  spend hits it.
- **virtual_keys** — `id, user_id → users, label, key_hash, key_prefix, allow_live,
  default_provider, monthly_budget_usd, budget_period, budget_start, budget_end, created_at,
  last_used_at, revoked_at`. Only the HMAC hash and an 11-char prefix are stored; the raw key is
  shown once. Per-key **budget**: `$X` per `1 day` / `1 week` / `1 month` (rolling), or a
  `custom` fixed `[budget_start, budget_end]` range → `402` when spent. Keys don't expire —
  revoke them explicitly. (`user_id` is added by a small boot-time migration on pre-multi-tenant
  databases and backfilled to the demo account.)
- **allowed_providers** — `(virtual_key_id, provider)`, unique. The per-key provider ACL enforced
  on every proxied request.
- **usage_logs** — `id, key_id, request_id, provider, model, prompt_tokens, completion_tokens,
  total_tokens, cost, cost_source, mode, simulated, latency_ms, status, prompt_preview,
  response_preview, ts`. `cost_source` = `provider` (real charge, e.g. OpenRouter `usage.cost`) or
  `configured` (tokens × price table). Previews are null unless `LOG_BODIES=true`.
- **provider_credentials** — `(id, user_id → users, provider, ciphertext, last4, updated_at)`,
  unique on `(user_id, provider)`. `user_id` set = that account's own key (its live-flagged
  virtual keys call real providers on it); `user_id` NULL = the admin-global key (only when
  `ALLOW_DB_PROVIDER_KEYS`, never in a deployed environment). `ciphertext` is a Fernet
  (AES-128-CBC + HMAC) token; the plaintext key is never returned by the API. A server env var for
  the same provider always wins.

On a deployed environment, provider credentials live only in server env vars — never in the DB,
never sent to the browser. `/api/providers` reports presence + liveness as booleans (+ `source`
and `last4` when a DB key is in play locally).

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
