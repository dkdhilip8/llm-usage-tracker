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
    def _reject_dev_secrets_when_deployed(self) -> "Settings":
        """Fail closed: a deployed ENVIRONMENT must not run on the built-in
        development ADMIN_TOKEN / SECRET_KEY."""
        if self.ENVIRONMENT.strip().lower() in _DEPLOYED_ENVS:
            offenders = []
            if self.ADMIN_TOKEN == _DEV_ADMIN_TOKEN:
                offenders.append("ADMIN_TOKEN")
            if self.SECRET_KEY == _DEV_SECRET_KEY:
                offenders.append("SECRET_KEY")
            if offenders:
                raise ValueError(
                    f"ENVIRONMENT={self.ENVIRONMENT!r} but {', '.join(offenders)} "
                    "still set to the built-in development default. Set a real "
                    "value (Render's blueprint generates one automatically)."
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
