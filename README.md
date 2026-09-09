# LLM Usage Tracker

A **multi-tenant LLM gateway**: each organization gets an isolated **Workspace**, issues virtual
API keys with per-provider access control, sends requests through the proxy, and tracks token
usage and cost on a dashboard. OpenAI · Anthropic · OpenRouter · Google Gemini.

> **Workspaces.** A user **signs up** (username + password, scrypt-hashed), then either
> **creates a Workspace** — becoming its **Workspace Admin** — or **joins one** with a
> single-use invite code as a **Team Member**. A user belongs to one Workspace at a time.
> Isolation is enforced server-side on every query: **a Workspace never reads another
> Workspace's keys, usage, requests, members, or provider config.**
>
> **Roles.**
> - **Workspace Admin** — workspace-wide usage + request log, all virtual keys, create / revoke
>   keys, budgets, assign keys to members, invite / remove members, manage the workspace's
>   provider keys and spend caps.
> - **Team Member** — a read-only Dashboard scoped to the virtual key(s) **assigned to them**.
>   No request log, no Playground, no member list, no provider config, no other members' data.
>
> **Real calls only — no simulator.** Every proxied request hits a real provider. A virtual key
> that is paused (`403`), whose workspace has no key for the target provider (`402`), or whose
> upstream call fails (`502`) returns an error — never a fake response.
>
> **Workspace live mode.** The Workspace Admin attaches the workspace's **own**
> OpenAI/Anthropic/OpenRouter/Gemini key(s) (encrypted at rest, shown back only as `····last4`),
> each with its **own monthly spend cap** (`LIVE_CAP_DEFAULT_USD` = $5 until set). The workspace's
> virtual keys call real providers billed to that key, blocked once that provider's monthly spend
> hits its cap. Each key has an **on/off** flag (`allow_live`) the admin toggles to pause billing
> without revoking. The admin hands the raw `vk_…` strings to whoever needs them — **recipients
> need no account**. A server env var for a provider configures it for every Workspace and always
> wins over an attached key.
>
> **Abuse controls:** `MAX_USERS`, signups & joins per IP/hour, per-workspace caps on keys /
> members / open invites / stored usage rows / requests-per-hour, and the server-enforced
> per-provider live-spend cap. No password reset yet.
>
> **Cost**: for **OpenRouter** calls it's the provider's *actual* charge (`usage.cost`).
> OpenAI/Anthropic/Gemini don't return a per-request cost, so those are **estimated** as
> tokens × a hand-maintained price table. Each row records which: `cost_source` = `provider`
> or `configured`.

```
Browser → React (Dashboard — all; Requests · Playground · Workspace — admin only)
        → FastAPI gateway
            → session + workspace membership  →  role (admin | member)
            → virtual-key auth  →  per-key provider ACL  →  per-key budget  →  workspace cap
            → real provider call   [JSON or SSE stream]   (402 / 403 / 502 on failure)
        → PostgreSQL (workspaces, workspace_invites, users, virtual_keys,
                      allowed_providers, provider_credentials, usage_logs)
        → dashboard aggregates + request log, scoped to the workspace (member: to assigned keys)
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

On first boot the backend creates the schema. First run:

1. **Create an account** at `/login`, then on **/welcome** choose **Create a workspace** — you
   become its Workspace Admin and land on an empty Dashboard.
2. On **Workspace**, attach a provider key (OpenRouter etc.), **create a virtual key** (allowed
   providers, optional budget, optionally *assign to* a Team Member), copy the `vk_…` string,
   open **Playground**, paste it, and send a request. It calls the real provider and shows on the
   workspace Dashboard and Requests. Without a provider key the request returns `402`.
3. **Invite a Team Member**: Workspace → *Generate invite* → send them the code. They sign up,
   pick **Join a workspace**, paste the code, and get a Dashboard scoped to the keys you assign
   them — nothing else.

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
J=/tmp/cj; rm -f $J
curl -s $BASE/healthz
# read endpoints require a session + workspace -> 401 for anon
curl -s -o /dev/null -w '%{http_code}\n' $BASE/api/usage/summary
# sign up + create a workspace (cookie jar carries the session)
curl -sc $J -X POST $BASE/api/auth/signup -H 'Content-Type: application/json' \
  -d '{"username":"smoke","password":"pw-abcdefgh"}' >/dev/null
curl -sb $J -c $J -X POST $BASE/api/workspace -H 'Content-Type: application/json' \
  -d '{"name":"Smoke Co"}' >/dev/null
# create a key scoped to openai + openrouter (allow_live on)
KEY=$(curl -sb $J -X POST $BASE/api/keys -H 'Content-Type: application/json' \
  -d '{"label":"smoke","allowed_providers":["openai","openrouter"],"allow_live":true}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["key"])')
# no provider key attached for openai -> 402 provider_not_configured
curl -s -o /dev/null -w '%{http_code}\n' -X POST $BASE/v1/proxy/chat \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"provider":"openai","model":"gpt-4o-mini","prompt":"hello"}'
# provider not on the key -> 403
curl -s -o /dev/null -w '%{http_code}\n' -X POST $BASE/v1/proxy/chat \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"provider":"anthropic","model":"claude-3-5-haiku","prompt":"hi"}'
# no key -> 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST $BASE/v1/proxy/chat \
  -H 'Content-Type: application/json' -d '{"provider":"openai","model":"gpt-4o","prompt":"x"}'
curl -sb $J $BASE/api/usage/summary
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
   Docker web service with `ENVIRONMENT=production` and generated `SECRET_KEY` + `ENCRYPTION_KEY`
   (the app refuses to boot on the dev-default `SECRET_KEY` once `ENVIRONMENT` is deployed).
   Creating the service by hand instead? Set `ENVIRONMENT`, `SECRET_KEY`, `ENCRYPTION_KEY`
   yourself.
4. Set `DATABASE_URL` on the service to the Neon connection string, then deploy.
5. Open `https://<service>.onrender.com/healthz` → `{"status":"ok",...}`. **Sign up**, then
   create a workspace or join one with an invite code. There is no privileged super-admin —
   every account is scoped to its own workspace.

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
| `ENVIRONMENT` | `development` | Set to `production` (or `staging`) on a deployed host. The app then **refuses to start** on the dev-default `SECRET_KEY`, and warns if `ENCRYPTION_KEY` is left to derive from it. |
| `DATABASE_URL` | local compose value | Postgres. `postgres://` / `postgresql://` auto-rewritten to the psycopg3 driver. |
| `SECRET_KEY` | `dev-secret` | HMAC pepper for virtual-key hashing **and** session-cookie signing. Must be overridden when `ENVIRONMENT` is deployed. |
| `ENCRYPTION_KEY` | `""` | Fernet key (urlsafe-base64, 32 bytes) encrypting each workspace's stored provider keys. Empty ⇒ derived from `SECRET_KEY`; set explicitly on a deployed host. |
| `SESSION_TTL_HOURS` | `168` | Session-cookie lifetime. |
| `ALLOW_SIGNUP` | `true` | Whether new accounts can be created. |
| `MAX_USERS` | `300` | Signup refused past this many accounts (whole instance). |
| `SIGNUPS_PER_IP_PER_HOUR` | `5` | In-process signup throttle per client IP. |
| `JOINS_PER_IP_PER_HOUR` | `10` | In-process workspace-join throttle per client IP. |
| `MAX_WORKSPACES` | `200` | Create-workspace refused past this many. |
| `MAX_MEMBERS_PER_WORKSPACE` | `25` | Join refused once a workspace is this full. |
| `MAX_KEYS_PER_WORKSPACE` | `25` | Virtual-key cap per workspace. |
| `MAX_OPEN_INVITES_PER_WORKSPACE` | `50` | Cap on un-consumed, un-expired invites. |
| `INVITE_TTL_DAYS` | `14` | Invite code lifetime. |
| `MAX_USAGE_ROWS_PER_WORKSPACE` | `20000` | The proxy stops recording once a workspace hits this. |
| `PLAYGROUND_REQUESTS_PER_HOUR` | `120` | Per-workspace proxy rate limit. |
| `ALLOW_LIVE_KEYS` | `true` | Let a Workspace Admin attach the workspace's own provider key. |
| `LIVE_CAP_DEFAULT_USD` | `5` | Default monthly live-spend cap for a provider with no explicit cap. Any non-negative per-provider value, no ceiling. |
| `CORS_ORIGINS` | `""` | Comma-separated origins; local dev only. |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` / `GEMINI_API_KEY` | `""` | Instance-wide provider creds — configure a provider for every workspace. Always win over a workspace's attached key. Never shown in the UI. With none set, a request `402`s until its workspace attaches a key. Gemini uses Google's OpenAI-compatible endpoint. |
| `PROVIDER_CHECK_TTL` | `300` | Seconds to cache a provider liveness check. |
| `LOG_BODIES` | `false` | Store truncated prompt/response previews on `usage_logs` for the Requests tab. Off by default — bodies can be sensitive. |

---

## API

Auth = a valid session cookie (`POST /api/auth/{signup,login}`). "member" = any user in a
workspace; "admin" = the Workspace Admin. Endpoints marked *member* also need workspace
membership (`403` if signed in but not in one).

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/healthz` | — | health / warmup (`{status, version}`) |
| GET | `/api/models` | — | configured pricing table (per provider/model) |
| POST | `/api/auth/signup` `\|` `/login` | — | `{username, password}` → `httpOnly` session cookie, `{authenticated, user:{id, username, workspace:{id,name,role}|null}}` |
| POST `\|` GET | `/api/auth/logout` `\|` `/me` | — | clear the cookie / current principal |
| GET `\|` DELETE | `/api/account` | user | `{username, workspace, live_cap_default_usd}` / delete my account (`400` if I'm the sole admin of a non-empty workspace) |
| GET | `/api/workspace` | member | admin: `{id, name, role, members[], invites[], providers[]}`; member: `{id, name, role}` |
| POST `\|` PATCH `\|` DELETE | `/api/workspace` | user / admin | create (`{name}`, `409` if already in one) / rename / delete (`409` unless no other members; cascades keys, usage, credentials, invites) |
| POST | `/api/workspace/join` | user | `{code}` → join as a Team Member. `404` invalid/expired, `409` used/already in a workspace, `429` throttled |
| POST | `/api/workspace/leave` | member | leave (Team Member only) — unassigns your keys |
| POST `\|` DELETE | `/api/workspace/invites[/{id}]` | admin | mint a single-use code (`{label?}` → `{code}`, shown once) / revoke an open one |
| DELETE | `/api/workspace/members/{user_id}` | admin | remove a Team Member — their keys are unassigned, their session loses access |
| GET | `/api/workspace/providers` | admin | `[{provider, configured, source(env\|workspace\|none), last4, monthly_cap_usd, spend_this_month}]` |
| PUT `\|` DELETE | `/api/workspace/providers/{provider}/key` | admin | attach / clear the workspace's encrypted provider key. `409` if a server env var is set |
| PATCH | `/api/workspace/providers/{provider}/cap` | admin | `{monthly_cap_usd}` (`null` → the default). `404` unless a key is attached |
| GET | `/api/providers` | admin | `[{provider, env_var, configured_via_env}]` — instance-wide env-var status only |
| POST | `/api/keys` | admin | `{label, allowed_providers[], allow_live?, default_provider?, assigned_user_id?, monthly_budget_usd?, budget_period?(day\|week\|month\|custom), budget_start?, budget_end?}` → raw key once |
| GET | `/api/keys` | member | admin → every key in the workspace (+ `assigned_username`); member → only keys assigned to them |
| PATCH `\|` DELETE | `/api/keys/{id}` | admin | patch (budget, `allow_live`, `assigned_user_id`/`clear_assignment`) / revoke |
| GET | `/api/requests` | admin | recent request log (`?limit&cursor&provider&model&key_id&status&start&end`) |
| GET | `/api/usage/summary` `\|` `/timeseries` `\|` `/by-key` `\|` `/by-model` | member | dashboard aggregates (`start,end,provider,model,key_id`); admin → whole workspace, member → assigned keys. `summary` adds `latency_p50_ms`, `latency_p95_ms`, `error_rate`, `tokens_per_sec` |
| **POST** | **`/v1/chat/completions`** | Bearer `vk_…` | **OpenAI-compatible.** `{model:"<provider>/<slug>", messages[], stream?}` → OpenAI `chat.completion` (or SSE chunks). `402` over budget/cap or no provider key, `403` paused / provider not on key, `502` upstream failure. |
| GET | `/v1/proxy/inspect` | Bearer `vk_…` | this key's allowed providers + `{provider, ready}` (ready = a workspace key exists) |
| POST | `/v1/proxy/chat` | Bearer `vk_…` | friendly shape used by the Playground: `{provider, model, prompt}` → completion + usage |

The proxy (`/v1/*`) requires a `vk_…` key and is workspace-agnostic — the key itself is the
credential.

---

## Data model

- **workspaces** — `id, name, created_at`. The tenant boundary. Deleting one cascades its keys,
  usage, provider credentials, and invites.
- **users** — `id, username (unique), password_hash (scrypt), workspace_id → workspaces
  (nullable, `ON DELETE SET NULL`), workspace_role (`admin` | `member` | NULL), created_at`. A
  user is in at most one workspace. Sessions are a stateless HMAC-signed cookie carrying the
  user id.
- **workspace_invites** — `id, workspace_id → workspaces, code (unique, `secrets.token_urlsafe`),
  label, created_at, expires_at, accepted_by_id → users, accepted_at`. Single-use: `join`
  consumes it with a guarded `UPDATE … WHERE accepted_at IS NULL`. "Open" = not accepted and not
  expired.
- **virtual_keys** — `id, workspace_id → workspaces (`ON DELETE CASCADE`), assigned_user_id →
  users (nullable), label, key_hash, key_prefix, allow_live, default_provider,
  monthly_budget_usd, budget_period, budget_start, budget_end, created_at, last_used_at,
  revoked_at`. `assigned_user_id` NULL = workspace-level (admin-only visibility); set = that
  Team Member sees it. Only the HMAC hash + an 11-char prefix are stored; the raw key is shown
  once. Per-key **budget**: `$X` per `1 day` / `1 week` / `1 month` (rolling), or a `custom`
  fixed `[budget_start, budget_end]` range → `402` when spent.
- **allowed_providers** — `(virtual_key_id, provider)`, unique. The per-key provider ACL enforced
  on every proxied request.
- **usage_logs** — `id, key_id, request_id, provider, model, prompt_tokens, completion_tokens,
  total_tokens, cost, cost_source, latency_ms, status, prompt_preview, response_preview, ts`
  (`mode` / `simulated` are legacy columns from the retired simulator — always `live` / `false`).
  Workspace ownership flows through `key_id → virtual_keys.workspace_id`. `cost_source` =
  `provider` (real charge) or `configured` (tokens × price table). `status` = `success` or
  `error` (a failed upstream call still records a row). Previews are null unless `LOG_BODIES=true`.
- **provider_credentials** — `(id, workspace_id → workspaces (`ON DELETE CASCADE`), provider,
  ciphertext, last4, monthly_cap_usd, updated_at)`, unique on `(workspace_id, provider)`. The
  workspace's own provider key; its `allow_live` virtual keys call real providers on it, and
  `enforce_workspace_cap` blocks calls on a provider once the calendar month's live spend on it
  hits `monthly_cap_usd` (NULL ⇒ `LIVE_CAP_DEFAULT_USD`). `ciphertext` is a Fernet
  (AES-128-CBC + HMAC) token; the plaintext is never returned by the API. A server env var for
  the same provider always wins.

There is no Alembic. `app/bootstrap.py::_migrate` runs guarded `information_schema` checks +
`ALTER TABLE`s on boot. The v6 migration adds the workspace tables/columns, moves each existing
account into a personal workspace as its admin (keys carried over), drops the old global-admin
account and `virtual_keys.user_id`.

---

## Future improvements

Per-key rate limits (RPM/TPM) · per-model budgets · webhook/Slack alerts · usage-anomaly
detection · Prometheus `/metrics` · pricing catalog auto-synced from
OpenRouter `/api/v1/models` · exact-match response cache · provider fallback on live error ·
Alembic migrations + backups · Redis for shared rate-limit / budget counters · SSO / org
hierarchy · OpenTelemetry traces.
