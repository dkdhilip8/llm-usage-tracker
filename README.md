# LLM Usage Tracker

An **LLM gateway**: issue per-user virtual API keys with per-provider access control, send
requests through the proxy, and track per-user token usage and cost on a dashboard.
OpenAI · Anthropic · OpenRouter · Google Gemini.

> **Dry-run by default.** With `ENABLE_LIVE=false` every request runs against a deterministic
> simulator — no provider calls, no cost — so you can wire keys and dashboards up before any
> real spend. Live calls are opt-in per key (see *Per-account live mode*).
>
> **Multi-tenant.** Each person **signs up** (username + password, scrypt-hashed) and gets their
> own virtual keys, a Playground, and a private dashboard scoped to their usage. Sign-in is
> required — Dashboard / Requests / Playground / Account all need a session. One privileged
> **admin** account (`ADMIN_USERNAME` + `ADMIN_PASSWORD`, or the `X-Admin-Token` header for
> curl / SDK / CI) sees every account's keys + usage and owns server-side provider config.
>
> **Per-account live mode.** On the Account page a signed-in user attaches their **own**
> OpenAI/Anthropic/OpenRouter/Gemini key(s) (encrypted at rest, shown back only as `····last4`),
> each with its **own monthly spend cap** (any non-negative value; `LIVE_CAP_DEFAULT_USD` = $5
> applies until they set one). A virtual key for a configured provider then makes real upstream
> calls billed to that key, blocked once that provider's monthly live spend hits its cap. They
> copy the raw `vk_…` strings and hand them to whoever needs them — **recipients need no
> account**. A key with no configured provider stays **simulated**.
>
> **Abuse controls:** `MAX_USERS`, signups/IP/hour, per-account caps on keys / stored usage
> rows / requests-per-hour, and the server-enforced per-provider live-spend cap. No password
> reset yet.
>
> **Provider keys** normally come from server env vars only. Outside production an admin can set
> `ALLOW_DB_PROVIDER_KEYS=true` and paste keys in the UI — stored **AES-encrypted** in the DB,
> shown back only as `····last4`, always overridden by an env var for the same provider.
> **Hard-blocked when `ENVIRONMENT` is deployed.**
>
> **Cost**: for **live OpenRouter** calls it's the provider's *actual* charge (`usage.cost` from
> the response). OpenAI/Anthropic/Gemini don't return a per-request cost, so those (and all
> simulated calls) are **estimated** as tokens × a hand-maintained price table. Each row records
> which: `cost_source` = `provider` or `configured`. The dashboard shows the actual/estimated
> split.
>
> A request only hits a real provider when `ENABLE_LIVE=true` **and** the virtual key has
> `allow_live` **and** that provider's key is configured and passes a liveness check. Otherwise
> it runs simulated.

```
Browser → React (Dashboard · Requests · Playground · Account — sign-in required)
        → FastAPI gateway
            → virtual-key auth  →  per-key provider ACL  →  per-key monthly budget
            → simulator (dry run)  |  real provider call (gated)   [JSON or SSE stream]
        → PostgreSQL (users, virtual_keys, allowed_providers, usage_logs)
        → dashboard aggregates + request log, scoped per account
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
  (Postgres service), tsc + vitest + build, and `docker build`. It is not committed yet — push
  it once the repo has GitHub Actions access (`gh auth refresh -h github.com -s workflow`).

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

On first boot the backend creates the schema and the admin user. First run:

1. **Create an account** at `/login` → you land on **/account** with an empty dashboard.
2. **Create a virtual key** (pick its allowed providers, optional budget), copy the `vk_…`
   string, then open **Playground**, paste it, and send a request. With `ENABLE_LIVE=false` it
   runs simulated; the request shows up on *your* Dashboard and Requests.
3. Sign in as **admin** (`admin` / `dev-password` under docker-compose) to see *all* accounts'
   keys + usage — the keys table gains an Owner column — and manage server-side provider keys.

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

`.github/workflows/ci.yml` runs all of the above plus `docker build` once committed.

### Smoke test

```bash
BASE=http://localhost:8000
curl -s $BASE/healthz
# read endpoints require a session -> 401 for anon
curl -s -o /dev/null -w '%{http_code}\n' $BASE/api/usage/summary
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
  -d '{"provider":"openai","model":"gpt-4o-mini","prompt":"hello"}'
# provider not on the key -> 403
curl -s -o /dev/null -w '%{http_code}\n' -X POST $BASE/v1/proxy/chat \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"provider":"anthropic","model":"claude-3-5-haiku","prompt":"hi"}'
# no key -> 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST $BASE/v1/proxy/chat \
  -H 'Content-Type: application/json' -d '{"provider":"openai","model":"gpt-4o","prompt":"x"}'
curl -s $BASE/api/usage/summary -H 'X-Admin-Token: dev-admin-token'
```

---

## Deploy (Render + Neon)

One Docker web service (FastAPI serves the built SPA) + one managed Postgres. It runs on the
Render and Neon free tiers; the only catch is a ~30–60 s cold start after 15 min idle (Render)
and a ~300 ms cold DB wake (Neon).

### Steps

1. Create a **Neon** project → copy the connection string.
2. Push this repo to GitHub.
3. Render → **New → Blueprint** → select the repo. It reads `render.yaml` and creates the
   Docker web service with `ENVIRONMENT=production` and generated `ADMIN_TOKEN` / `SECRET_KEY` /
   `ADMIN_PASSWORD` (the app refuses to boot on the dev defaults, or without a ≥12-char
   `ADMIN_PASSWORD`, once `ENVIRONMENT` is deployed). Creating the service by hand instead? Set
   `ENVIRONMENT`, `ADMIN_TOKEN`, `SECRET_KEY`, `ADMIN_PASSWORD` yourself.
4. Set `DATABASE_URL` on the service to the Neon connection string, then deploy.
5. Open `https://<service>.onrender.com/healthz` → `{"status":"ok",...}`. **Sign up** for an
   account, or sign in as **admin** with `admin` + the generated `ADMIN_PASSWORD` (Render →
   service → Environment) to see all accounts and manage server-side provider keys.

**All-Render fallback:** uncomment the `databases:` block in `render.yaml`. Note Render's free
Postgres is deleted 30 days after creation; the schema is recreated automatically but any keys
and usage you added are lost.

### Keeping it warm

```bash
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" https://<service>.onrender.com/healthz
```

Or point an UptimeRobot / cron-job.org monitor at `/healthz` every 10 minutes.

---

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | `development` | Set to `production` (or `staging`) on a deployed host. The app then **refuses to start** unless `ADMIN_TOKEN` + `SECRET_KEY` are non-default, `ADMIN_PASSWORD` is set (≥12 chars), and `ALLOW_DB_PROVIDER_KEYS` is off. |
| `DATABASE_URL` | local compose value | Postgres. `postgres://` / `postgresql://` auto-rewritten to the psycopg3 driver. |
| `ADMIN_TOKEN` | `dev-admin-token` | `X-Admin-Token` header credential (curl / SDK / CI). Must be overridden when `ENVIRONMENT` is deployed. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `""` | Admin login form. Empty password ⇒ password login disabled (token still works). `ADMIN_PASSWORD` required (≥12 chars) when `ENVIRONMENT` is deployed. |
| `SESSION_TTL_HOURS` | `168` | Session-cookie lifetime (users + admin). |
| `ALLOW_SIGNUP` | `true` | Whether new accounts can be created. |
| `MAX_USERS` | `300` | Signup is refused past this many accounts. |
| `SIGNUPS_PER_IP_PER_HOUR` | `5` | In-process signup throttle per client IP. |
| `MAX_KEYS_PER_USER` | `10` | Virtual-key cap per non-admin account. |
| `MAX_USAGE_ROWS_PER_USER` | `4000` | The proxy stops recording once an account hits this. |
| `PLAYGROUND_REQUESTS_PER_HOUR` | `120` | Per-account proxy rate limit (non-admin). |
| `ALLOW_LIVE_KEYS` | `true` | Let users attach their own provider key on the Account page to enable live mode for their virtual keys. |
| `LIVE_CAP_DEFAULT_USD` | `5` | Default monthly live-spend cap for a provider key with no explicit cap. Users can set any non-negative per-provider value on the Account page (no ceiling). |
| `SECRET_KEY` | `dev-secret` | HMAC pepper for virtual-key hashing **and** session-cookie signing. Must be overridden when `ENVIRONMENT` is deployed. |
| `CORS_ORIGINS` | `""` | Comma-separated origins; local dev only. |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` / `GEMINI_API_KEY` | `""` | Server-side provider creds. Always win over a DB-stored key. Never shown in the UI. Gemini uses Google's OpenAI-compatible endpoint. |
| `ALLOW_DB_PROVIDER_KEYS` | `false` | Let the Admin UI store provider keys (AES-encrypted) in the DB. Convenience for non-production — **hard-blocked when `ENVIRONMENT` is deployed**. |
| `ENCRYPTION_KEY` | `""` | Fernet key (urlsafe-base64, 32 bytes) for that encryption. Empty ⇒ derived from `SECRET_KEY`. |
| `ENABLE_LIVE` | `false` | Master switch for real upstream calls. When off, every request runs simulated. When on, a request still needs its virtual key flagged `allow_live` **and** a configured provider key, and it stops at that provider's monthly cap. |
| `PROVIDER_CHECK_TTL` | `300` | Seconds to cache a provider liveness check. |
| `LOG_BODIES` | `false` | Store truncated prompt/response previews on `usage_logs` for the Requests tab. Off by default — bodies can be sensitive. |
| `SIMULATE_LATENCY_SLEEP` | `true` | In dry-run mode, sleep for the simulated latency. Tests set `false`. |

---

## API

Auth = a valid session cookie (`POST /api/auth/{signup,login}`) **or** the `X-Admin-Token` header
(→ the admin user). "user" below = any signed-in account; "admin" = the privileged one. Every
`/api/*` endpoint below needs a session. Read endpoints are **viewer-scoped**: user → their own;
admin → all.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/healthz` | — | health / warmup (`{version, live_enabled, db_keys_enabled}`) |
| GET | `/api/models` | — | configured pricing table (per provider/model) |
| POST | `/api/auth/signup` `\|` `/login` | — | `{username, password}` → sets `httpOnly` session cookie, `{authenticated, user:{id,username,is_admin,can_live}}` |
| POST `\|` GET | `/api/auth/logout` `\|` `/me` | — | clear the cookie / current principal |
| DELETE `\|` DELETE | `/api/account/data` `\|` `/api/account` | user | wipe my keys+usage / delete my account |
| GET | `/api/account` | user | my account: `username`, `can_live`, `live_cap_default_usd`, and `providers[]` (`source`, `last4`, `monthly_cap_usd`, `spend_this_month`) |
| PUT `\|` DELETE | `/api/account/providers/{provider}/key` | user | attach / clear **my own** encrypted provider key. `409` if a server env var is set for that provider |
| PATCH | `/api/account/providers/{provider}/cap` | user | set `{monthly_cap_usd}` for that provider (`null` clears → the default). `404` unless a key for that provider is attached |
| GET | `/api/providers` | admin | `[{provider, env_var, configured, valid, source, last4, checked_at}]` (`?refresh=true` to re-check) |
| PUT `\|` DELETE | `/api/providers/{provider}/key` | admin | set / clear a DB-stored (encrypted) provider key. `404` unless `ALLOW_DB_PROVIDER_KEYS`; `409` if a server env var is set for that provider |
| POST | `/api/keys` | user | `{label, allowed_providers[], allow_live?(needs a configured provider key), default_provider?, monthly_budget_usd?, budget_period?(day\|week\|month\|custom), budget_start?, budget_end?}` → raw key once |
| GET `\|` PATCH `\|` DELETE | `/api/keys[/{id}]` | user | list *your* keys (admin: all, + `owner_username`) / patch / revoke |
| GET | `/api/requests` | user *(scoped)* | recent request log (`?limit&cursor&provider&model&key_id&status&mode&start&end`) |
| **POST** | **`/v1/chat/completions`** | Bearer `vk_…` | **OpenAI-compatible.** `{model:"<provider>/<slug>", messages[], stream?}` → OpenAI `chat.completion` (or SSE chunks). `402` over budget, `403` provider not on key. |
| GET | `/v1/proxy/inspect` | Bearer `vk_…` | this key's allowed providers + per-provider `mode` (`simulated`/`live`) |
| POST | `/v1/proxy/chat` | Bearer `vk_…` | friendly shape used by the Playground: `{provider, model, prompt}` → completion + usage |
| GET | `/api/usage/summary` `\|` `/timeseries` `\|` `/by-key` `\|` `/by-model` | user *(scoped)* | dashboard aggregates (`start,end,provider,model,key_id`); `summary` adds `latency_p50_ms`, `latency_p95_ms`, `error_rate`, `tokens_per_sec` |

Every `/api/*` route requires a session cookie or the `X-Admin-Token` header; read routes are
viewer-scoped (user → their own, admin → all). The proxy (`/v1/*`) requires a `vk_…` key.

---

## Data model

- **users** — `id, username (unique), password_hash (scrypt), is_admin, created_at`. The admin row
  is created/updated from `ADMIN_USERNAME`/`ADMIN_PASSWORD` on boot. Sessions are a stateless
  HMAC-signed cookie carrying the user id.
- **virtual_keys** — `id, user_id → users, label, key_hash, key_prefix, allow_live,
  default_provider, monthly_budget_usd, budget_period, budget_start, budget_end, created_at,
  last_used_at, revoked_at`. Only the HMAC hash and an 11-char prefix are stored; the raw key is
  shown once. Per-key **budget**: `$X` per `1 day` / `1 week` / `1 month` (rolling), or a
  `custom` fixed `[budget_start, budget_end]` range → `402` when spent. Keys don't expire —
  revoke them explicitly.
- **allowed_providers** — `(virtual_key_id, provider)`, unique. The per-key provider ACL enforced
  on every proxied request.
- **usage_logs** — `id, key_id, request_id, provider, model, prompt_tokens, completion_tokens,
  total_tokens, cost, cost_source, mode, simulated, latency_ms, status, prompt_preview,
  response_preview, ts`. `cost_source` = `provider` (real charge, e.g. OpenRouter `usage.cost`) or
  `configured` (tokens × price table). Previews are null unless `LOG_BODIES=true`.
- **provider_credentials** — `(id, user_id → users, provider, ciphertext, last4, monthly_cap_usd,
  updated_at)`, unique on `(user_id, provider)`. `user_id` set = that account's own key (its
  live-flagged virtual keys call real providers on it) with its own monthly live-spend cap
  (`monthly_cap_usd` NULL ⇒ `LIVE_CAP_DEFAULT_USD`); `enforce_account_cap` blocks live calls on a
  provider once the calendar month's `mode='live'` spend on it hits that cap. `user_id` NULL =
  the admin-global key (only when `ALLOW_DB_PROVIDER_KEYS`, never in a deployed environment).
  `ciphertext` is a Fernet (AES-128-CBC + HMAC) token; the plaintext key is never returned by the
  API. A server env var for the same provider always wins.

On a deployed environment, provider credentials live only in server env vars — never in the DB,
never sent to the browser. `/api/providers` reports presence + liveness as booleans (+ `source`
and `last4` when a DB key is in play locally).

There is no Alembic. `app/bootstrap.py::_migrate` runs a few guarded `information_schema` checks
+ `ALTER TABLE`s on boot; `bootstrap()` also creates/updates the single admin user.

---

## Future improvements

Per-key rate limits (RPM/TPM) · per-model budgets · webhook/Slack alerts · usage-anomaly
detection · Prometheus `/metrics` · pricing catalog auto-synced from
OpenRouter `/api/v1/models` · exact-match response cache · provider fallback on live error ·
Alembic migrations + backups · Redis for shared rate-limit / budget counters · SSO / org
hierarchy · OpenTelemetry traces.
