from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Built-in development defaults. Fine locally; refused when ENVIRONMENT is a
# deployed one (see _reject_dev_secrets_when_deployed).
_DEV_ADMIN_TOKEN = "dev-admin-token"
_DEV_SECRET_KEY = "dev-secret"
_DEPLOYED_ENVS = {"production", "prod", "staging"}


class Settings(BaseSettings):
    """Runtime configuration. All values have demo-safe defaults so the app boots
    with zero env vars for local experimentation."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Deployment context. "development" (default) keeps the dev-default secrets
    # usable; a deployed value (production/staging) makes them a hard startup error.
    ENVIRONMENT: str = "development"

    # ---- admin account (the one is_admin=True user, created/updated on boot) ----
    ADMIN_USERNAME: str = "admin"  # also the admin user's login email
    # Empty => admin password login disabled (the X-Admin-Token header still works).
    # Required (min 12 chars) when ENVIRONMENT is deployed.
    ADMIN_PASSWORD: str = ""
    # Signed session-cookie lifetime.
    SESSION_TTL_HOURS: int = 168

    # ---- public multi-tenant signup ----
    ALLOW_SIGNUP: bool = True
    MAX_USERS: int = 300  # reject signup past this (protects the free DB)
    SIGNUPS_PER_IP_PER_HOUR: int = 5
    MAX_KEYS_PER_USER: int = 10
    MAX_USAGE_ROWS_PER_USER: int = 4000  # proxy stops recording past this
    PLAYGROUND_REQUESTS_PER_HOUR: int = 120

    # ---- workspaces (team gateway: one owner's provider key, shared by members) ----
    ALLOW_WORKSPACES: bool = True
    MAX_WORKSPACES: int = 100
    MAX_WORKSPACE_MEMBERS: int = 10  # excludes the owner
    WORKSPACE_CAP_MAX_USD: float = 10.0  # hard ceiling on a workspace's monthly spend cap
    WORKSPACE_DEFAULT_CAP_USD: float = 5.0

    # ---- provider-credential encryption (only used when ALLOW_DB_PROVIDER_KEYS) ----
    # A urlsafe-base64 32-byte Fernet key. Empty => derived from SECRET_KEY.
    ENCRYPTION_KEY: str = ""
    # Let an admin store provider API keys in the DB (encrypted) via the UI.
    # HARD-BLOCKED in a deployed ENVIRONMENT — public deploys use server env vars only.
    ALLOW_DB_PROVIDER_KEYS: bool = False

    # Postgres. Render/Neon hand out `postgres://` or `postgresql://`; `sqlalchemy_url`
    # rewrites the scheme to the psycopg3 driver.
    DATABASE_URL: str = "postgresql+psycopg://llm:llm@localhost:5432/llmtracker"

    # Shared secret for the admin API (create/list/revoke keys). Sent as `X-Admin-Token`.
    ADMIN_TOKEN: str = "dev-admin-token"

    # HMAC pepper for virtual-key hashing. Never stored alongside the hash.
    SECRET_KEY: str = "dev-secret"

    # Comma-separated origins for local dev (Vite on :5173). Empty in prod (same origin).
    CORS_ORIGINS: str = ""

    # ---- provider credentials (server-side only; never in the DB or UI) ----
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""

    # Master switch for live upstream calls. Default off keeps the public demo $0.
    # Even when on, a request also needs key.allow_live + a configured & valid provider.
    ENABLE_LIVE: bool = False

    # Seconds to cache a provider liveness check.
    PROVIDER_CHECK_TTL: int = 300

    # Store truncated prompt/response text on usage_logs for the Requests log view.
    # Off by default — request bodies can contain sensitive data.
    LOG_BODIES: bool = False

    # Simulator sleeps to mimic real latency. Tests set this false for speed.
    SIMULATE_LATENCY_SLEEP: bool = True

    # On boot, if usage_logs is empty, generate the shared demo dataset (keys +
    # ~30 days of simulated usage + a recent anomaly). Set true on the public
    # deploy so a fresh database self-populates; false locally and in tests.
    SEED_DEMO_DATA: bool = False

    VERSION: str = "0.7.0"

    @model_validator(mode="after")
    def _validate_deployment(self) -> "Settings":
        """Fail closed on unsafe config in a deployed ENVIRONMENT."""
        if self.ENVIRONMENT.strip().lower() not in _DEPLOYED_ENVS:
            return self
        errors = []
        if self.ADMIN_TOKEN == _DEV_ADMIN_TOKEN:
            errors.append("ADMIN_TOKEN is still the built-in development default")
        if self.SECRET_KEY == _DEV_SECRET_KEY:
            errors.append("SECRET_KEY is still the built-in development default")
        if not self.ADMIN_PASSWORD:
            errors.append("ADMIN_PASSWORD must be set (admin login front door)")
        elif len(self.ADMIN_PASSWORD) < 12:
            errors.append("ADMIN_PASSWORD must be at least 12 characters")
        if self.ALLOW_DB_PROVIDER_KEYS:
            errors.append(
                "ALLOW_DB_PROVIDER_KEYS must be off in a deployed environment — "
                "use server env vars for provider credentials"
            )
        if errors:
            raise ValueError(
                f"ENVIRONMENT={self.ENVIRONMENT!r}: " + "; ".join(errors)
            )
        return self

    @property
    def sqlalchemy_url(self) -> str:
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    def provider_api_key(self, provider: str) -> str:
        return {
            "openai": self.OPENAI_API_KEY,
            "anthropic": self.ANTHROPIC_API_KEY,
            "openrouter": self.OPENROUTER_API_KEY,
        }.get(provider, "")


settings = Settings()
